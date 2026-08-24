import discord
from discord import app_commands
import sqlite3
import time
import os
import threading
import re
from http.server import SimpleHTTPRequestHandler, HTTPServer

db = sqlite3.connect('kuv_social_media.db')
cursor = db.cursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS posts 
                  (post_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, username TEXT, content TEXT, image_url TEXT, likes_count INTEGER, reposts_count INTEGER, bot_likes INTEGER DEFAULT 0, timestamp REAL)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS likes (user_id TEXT, post_id INTEGER)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS reposts (user_id TEXT, post_id INTEGER)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS replies 
                  (reply_id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, user_id TEXT, username TEXT, content TEXT, timestamp REAL)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS followers 
                  (follower_id TEXT, following_id TEXT, PRIMARY KEY (follower_id, following_id))''')

cursor.execute('''CREATE TABLE IF NOT EXISTS user_profiles 
                  (user_id TEXT PRIMARY KEY, custom_username TEXT, bio TEXT DEFAULT 'No bio set.', joined_timestamp REAL)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS hashtags 
                  (tag TEXT, post_id INTEGER, PRIMARY KEY (tag, post_id))''')
db.commit()

ADMIN_USER_IDS = ["968948615726911488", "1211031738839728132"]
def index_hashtags(post_id, text):
    tags = re.findall(r"#(\w+)", text.lower())
    for tag in set(tags):
        cursor.execute("INSERT OR IGNORE INTO hashtags (tag, post_id) VALUES (?, ?)", (tag, post_id))
    db.commit()

def get_kuv_username(user_id, fallback_name):
    cursor.execute("SELECT custom_username FROM user_profiles WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    if res and res[0]:
        return res[0]
    return fallback_name

class ReplyModal(discord.ui.Modal, title="💬 Drop Your Tea / Reply"):
    reply_text = discord.ui.TextInput(
        label="What's the word on the netz?",
        style=discord.TextStyle.paragraph,
        placeholder="Type your reply here...",
        required=True,
        max_length=500
    )

    def __init__(self, post_id):
        super().__init__()
        self.post_id = post_id

    async def on_submit(self, interaction: discord.Interaction):
        username = get_kuv_username(str(interaction.user.id), interaction.user.display_name)
        cursor.execute("INSERT INTO replies (post_id, user_id, username, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                       (self.post_id, str(interaction.user.id), username, self.reply_text.value, time.time()))
        db.commit()
        await interaction.response.send_message(f"🤫 Your reply has been indexed onto Feed #{self.post_id}!", ephemeral=True)
class SocialFeedButtons(discord.ui.View):
    def __init__(self, post_id):
        super().__init__(timeout=None)
        self.post_id = post_id

    @discord.ui.button(label="💖 Like", style=discord.ButtonStyle.grey, custom_id="social_like_btn")
    async def like(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        cursor.execute("SELECT * FROM likes WHERE user_id = ? AND post_id = ?", (user_id, self.post_id))
        liked = cursor.fetchone()
        if liked:
            cursor.execute("DELETE FROM likes WHERE user_id = ? AND post_id = ?", (user_id, self.post_id))
            cursor.execute("UPDATE posts SET likes_count = likes_count - 1 WHERE post_id = ?", (self.post_id,))
        else:
            cursor.execute("INSERT INTO likes (user_id, post_id) VALUES (?, ?)", (user_id, self.post_id))
            cursor.execute("UPDATE posts SET likes_count = likes_count + 1 WHERE post_id = ?", (self.post_id,))
        db.commit()
        cursor.execute("SELECT likes_count, bot_likes FROM posts WHERE post_id = ?", (self.post_id,))
        likes, bot_likes = cursor.fetchone()
        button.label = f"💖 Like ({likes + bot_likes})"
        await interaction.message.edit(view=self)
        await interaction.response.defer()

    @discord.ui.button(label="🔁 Repost", style=discord.ButtonStyle.grey, custom_id="social_repost_btn")
    async def repost(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        cursor.execute("SELECT * FROM reposts WHERE user_id = ? AND post_id = ?", (user_id, self.post_id))
        reposted = cursor.fetchone()
        if reposted:
            cursor.execute("DELETE FROM reposts WHERE user_id = ? AND post_id = ?", (user_id, self.post_id))
            cursor.execute("UPDATE posts SET reposts_count = reposts_count - 1 WHERE post_id = ?", (self.post_id,))
        else:
            cursor.execute("INSERT INTO reposts (user_id, post_id) VALUES (?, ?)", (user_id, self.post_id))
            cursor.execute("UPDATE posts SET reposts_count = reposts_count + 1 WHERE post_id = ?", (self.post_id,))
        db.commit()
        cursor.execute("SELECT reposts_count FROM posts WHERE post_id = ?", (self.post_id,))
        reps = cursor.fetchone()[0]
        button.label = f"🔁 Repost ({reps})"
        await interaction.message.edit(view=self)
        await interaction.response.defer()

    @discord.ui.button(label="💬 Reply", style=discord.ButtonStyle.blurple, custom_id="social_reply_btn")
    async def reply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ReplyModal(self.post_id))

    @discord.ui.button(label="🔎 View Thread", style=discord.ButtonStyle.success, custom_id="social_thread_btn")
    async def view_thread(self, interaction: discord.Interaction, button: discord.ui.Button):
        cursor.execute("SELECT username, content, image_url FROM posts WHERE post_id = ?", (self.post_id,))
        post_data = cursor.fetchone()
        if not post_data:
            return await interaction.response.send_message("❌ This post no longer exists.", ephemeral=True)
        p_user, p_content, img_url = post_data
        cursor.execute("SELECT username, content FROM replies WHERE post_id = ? ORDER BY timestamp ASC", (self.post_id,))
        replies_list = cursor.fetchall()
        reply_tree = ""
        if replies_list:
            for r_user, r_content in replies_list:
                reply_tree += f"\n**@{r_user}**: {r_content}\n"
        else:
            reply_tree = "*No replies on this post yet.*"
        thread_embed = discord.Embed(title=f"💬 THREAD VIEW • Feed #{self.post_id}", description=f"**@{p_user}**: {p_content}", color=0xffa500)
        thread_embed.add_field(name="─── 💬 REPLIES ───", value=reply_tree, inline=False)
        if img_url:
            thread_embed.set_image(url=img_url)
        await interaction.response.send_message(embed=thread_embed, ephemeral=True)
class Bot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True 
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        print("⚡ KUV Advanced Framework Initialized!")

bot = Bot()

@bot.tree.command(name="post", description="Post an official update to the KUV Universe!")
async def post(interaction: discord.Interaction, text_content: str, upload_art_url: str = None):
    user_id = str(interaction.user.id)
    username = get_kuv_username(user_id, interaction.user.display_name)
    cursor.execute("INSERT INTO posts (user_id, username, content, image_url, likes_count, reposts_count, bot_likes, timestamp) VALUES (?, ?, ?, ?, 0, 0, 0, ?)",
                   (user_id, username, text_content, upload_art_url, time.time()))
    post_id = cursor.lastrowid
    db.commit()
    index_hashtags(post_id, text_content)
    await interaction.response.send_message(f"✨ Posted successfully! Assigned Feed ID: #{post_id}.", ephemeral=True)

@bot.tree.command(name="delete_post", description="Delete an active post from the KUV framework.")
async def delete_post(interaction: discord.Interaction, post_id: int):
    user_id = str(interaction.user.id)
    cursor.execute("SELECT user_id FROM posts WHERE post_id = ?", (post_id,))
    res = cursor.fetchone()
    if not res:
        return await interaction.response.send_message("❌ This Post ID could not be found.", ephemeral=True)
    if res[0] != user_id and user_id not in ADMIN_USER_IDS:
        return await interaction.response.send_message("❌ You do not have permission to delete this post.", ephemeral=True)
    cursor.execute("DELETE FROM posts WHERE post_id = ?", (post_id,))
    cursor.execute("DELETE FROM likes WHERE post_id = ?", (post_id,))
    cursor.execute("DELETE FROM reposts WHERE post_id = ?", (post_id,))
    cursor.execute("DELETE FROM replies WHERE post_id = ?", (post_id,))
    cursor.execute("DELETE FROM hashtags WHERE post_id = ?", (post_id,))
    db.commit()
    await interaction.response.send_message(f"✅ Feed ID #{post_id} has been completely wiped from the database.", ephemeral=True)

@bot.tree.command(name="show_post", description="Pull up a specific post card by its exact Feed ID.")
async def show_post(interaction: discord.Interaction, post_id: int):
    cursor.execute("SELECT username, content, image_url, likes_count, reposts_count, bot_likes FROM posts WHERE post_id = ?", (post_id,))
    res = cursor.fetchone()
    if not res:
        return await interaction.response.send_message("❌ That Post ID does not exist.", ephemeral=True)
    username, content, img_url, likes, reps, b_likes = res
    embed = discord.Embed(title=f"📝 KUV SYSTEM • @{username}", description=content, color=0xffa500)
    embed.set_footer(text=f"Feed ID: #{post_id} • 💖 {likes + b_likes} Likes • 🔁 {reps} Reposts")
    if img_url:
        embed.set_image(url=img_url)
    await interaction.response.send_message(embed=embed, view=SocialFeedButtons(post_id))
@bot.tree.command(name="search_post", description="Search the KUV feed archive for words or text strings.")
async def search_post(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    cursor.execute("SELECT post_id, username, content, image_url, likes_count, reposts_count, bot_likes FROM posts WHERE content LIKE ? ORDER BY timestamp DESC LIMIT 3", (f"%{query}%",))
    matches = cursor.fetchall()
    if not matches:
        return await interaction.followup.send(f"❌ No matching results found for text string: '{query}'", ephemeral=True)
    await interaction.followup.send(f"🔎 Displaying top archive search results for: **'{query}'**")
    for row in matches:
        post_id, username, content, img_url, likes, reps, b_likes = row
        embed = discord.Embed(title=f"📝 RETRIEVED FEED • @{username}", description=content, color=0xffa500)
        embed.set_footer(text=f"Feed ID: #{post_id} • 💖 {likes + b_likes} Likes")
        if img_url:
            embed.set_image(url=img_url)
        await interaction.channel.send(embed=embed, view=SocialFeedButtons(post_id))

@bot.tree.command(name="hashtags", description="Search for trends using a specific hashtag string (do not include #).")
async def hashtags(interaction: discord.Interaction, tag: str):
    await interaction.response.defer()
    clean_tag = tag.strip().lower().replace("#", "")
    cursor.execute("SELECT p.post_id, p.username, p.content, p.image_url FROM posts p JOIN hashtags h ON p.post_id = h.post_id WHERE h.tag = ? ORDER BY p.timestamp DESC LIMIT 3", (clean_tag,))
    trends = cursor.fetchall()
    if not trends:
        return await interaction.followup.send(f"❌ The hashtag trend `#{clean_tag}` has no recorded entries yet.", ephemeral=True)
    await interaction.followup.send(f"🔥 Displaying top trending timeline modules for: **#{clean_tag}**")
    for row in trends:
        post_id, username, content, img_url = row
        embed = discord.Embed(title=f"🏷️ TREND MATRIX • @{username}", description=content, color=0xffa500)
        embed.set_footer(text=f"Feed ID: #{post_id}")
        if img_url:
            embed.set_image(url=img_url)
        await interaction.channel.send(embed=embed, view=SocialFeedButtons(post_id))

@bot.tree.command(name="set_username", description="Set a custom social handle handle for your KUV profile card!")
async def set_username(interaction: discord.Interaction, custom_username: str):
    user_id = str(interaction.user.id)
    clean_name = custom_username.strip().replace(" ", "_")
    if not clean_name.isalnum() and "_" not in clean_name:
        return await interaction.response.send_message("❌ Usernames must only contain letters, numbers, or underscores.", ephemeral=True)
    cursor.execute("SELECT user_id FROM user_profiles WHERE custom_username = ? AND user_id != ?", (clean_name, user_id))
    taken = cursor.fetchone()
    if taken:
        return await interaction.response.send_message(f"❌ Handle `@{clean_name}` is already registered to another user profile.", ephemeral=True)
    cursor.execute("INSERT OR IGNORE INTO user_profiles (user_id, joined_timestamp) VALUES (?, ?)", (user_id, time.time()))
    cursor.execute("UPDATE user_profiles SET custom_username = ? WHERE user_id = ?", (clean_name, user_id))
    db.commit()
    await interaction.response.send_message(f"✅ Your official KUV handle is now set to: **@{clean_name}**", ephemeral=True)

@bot.tree.command(name="fyp", description="View the top trending posts on the KUV algorithm!")
async def fyp(interaction: discord.Interaction):
    await interaction.response.defer()
    cursor.execute("SELECT post_id, username, content, image_url, likes_count, reposts_count, bot_likes FROM posts ORDER BY (likes_count + bot_likes + (reposts_count * 2)) DESC LIMIT 5")
    trending_posts = cursor.fetchall()
    if not trending_posts:
        return await interaction.followup.send("🦋 The FYP is empty right now. Be the first to use `/post`!", ephemeral=True)
    for row in trending_posts:
        post_id, username, content, img_url, likes, reps, b_likes = row
        embed = discord.Embed(title=f"🔥 TRENDING ON KUV • @{username}", description=content, color=0xffa500)
        embed.set_footer(text=f"Feed ID: #{post_id} • 💖 {likes + b_likes} Likes • 🔁 {reps} Reposts")
        if img_url:
            embed.set_image(url=img_url)
        await interaction.followup.send(embed=embed, view=SocialFeedButtons(post_id))

@bot.tree.command(name="profile", description="View a user profile card and core engagement statistics.")
async def profile(interaction: discord.Interaction, target_user: discord.Member = None):
    user = target_user or interaction.user
    user_id = str(user.id)
    cursor.execute("INSERT OR IGNORE INTO user_profiles (user_id, joined_timestamp) VALUES (?, ?)", (user_id, time.time()))
    db.commit()
    username = get_kuv_username(user_id, user.display_name)
    cursor.execute("SELECT bio FROM user_profiles WHERE user_id = ?", (user_id,))
    bio = cursor.fetchone()
    cursor.execute("SELECT COUNT(*) FROM posts WHERE user_id = ?", (user_id,))
    total_posts = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM followers WHERE following_id = ?", (user_id,))
    followers_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM followers WHERE follower_id = ?", (user_id,))
    following_count = cursor.fetchone()[0]
    profile_embed = discord.Embed(title=f"📇 KUV PROFILE • @{username}", description=f"*{bio[0] if bio and bio[0] else 'No bio set.'}*", color=0xffa500)
    profile_embed.set_thumbnail(url=user.display_avatar.url)
    profile_embed.add_field(name="📝 Posts", value=str(total_posts), inline=True)
    profile_embed.add_field(name="👥 Followers", value=str(followers_count), inline=True)
    profile_embed.add_field(name="➕ Following", value=str(following_count), inline=True)
    await interaction.response.send_message(embed=profile_embed)

@bot.tree.command(name="set_bio", description="Update your official KUV profile bio!")
async def set_bio(interaction: discord.Interaction, custom_bio: str):
    user_id = str(interaction.user.id)
    cursor.execute("INSERT OR IGNORE INTO user_profiles (user_id, joined_timestamp) VALUES (?, ?)", (user_id, time.time()))
    cursor.execute("UPDATE user_profiles SET bio = ? WHERE user_id = ?", (custom_bio, user_id))
    db.commit()
    await interaction.response.send_message("✅ Your profile bio has been updated successfully!", ephemeral=True)

@bot.tree.command(name="follow", description="Follow a KUV netz account to track their clout updates!")
async def follow(interaction: discord.Interaction, target_user: discord.Member):
    follower_id = str(interaction.user.id)
    following_id = str(target_user.id)
    if follower_id == following_id:
        return await interaction.response.send_message("❌ You cannot follow your own account.", ephemeral=True)
    cursor.execute("SELECT * FROM followers WHERE follower_id = ? AND following_id = ?", (follower_id, following_id))
    already_following = cursor.fetchone()
    if already_following:
        cursor.execute("DELETE FROM followers WHERE follower_id = ? AND following_id = ?", (follower_id, following_id))
        db.commit()
        return await interaction.response.send_message(f"💔 Unfollowed @{target_user.display_name}.", ephemeral=True)
    cursor.execute("INSERT INTO followers (follower_id, following_id) VALUES (?, ?)", (follower_id, following_id))
    db.commit()
    await interaction.response.send_message(f"✅ You are now officially following @{target_user.display_name}!", ephemeral=True)

@bot.tree.command(name="add_likes", description="Staff Only: Synchronize core database index packets.")
async def add_likes(interaction: discord.Interaction, post_id: int, amount: int):
    if str(interaction.user.id) not in ADMIN_USER_IDS:
        return await interaction.response.send_message("❌ You do not have permission to view the post likes.", ephemeral=True)
    cursor.execute("UPDATE posts SET bot_likes = bot_likes + ? WHERE post_id = ?", (amount, post_id))
    db.commit()
    await interaction.response.send_message(f"⚙️ `[SYSTEM]` Action logged. Metric index synchronized for Feed ID #{post_id}.", ephemeral=True)

def run_web_server():
    port = int(os.getenv("PORT", 8080))
    HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler).serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()
bot.run(os.getenv("TOKEN_PLACEHOLDER"))
