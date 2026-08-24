import discord
from discord import app_commands
import sqlite3
import time
import os
import threading
import re
from http.server import SimpleHTTPRequestHandler, HTTPServer

# 💾 CONNECT TO EXPANDED NETWORK DATABASE
db = sqlite3.connect('kuv_social_media.db')
cursor = db.cursor()

# Core App Tables
cursor.execute('''CREATE TABLE IF NOT EXISTS posts 
                  (post_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, username TEXT, content TEXT, image_url TEXT, likes_count INTEGER, reposts_count INTEGER, bot_likes INTEGER DEFAULT 0, timestamp REAL)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS likes (user_id TEXT, post_id INTEGER)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS reposts (user_id TEXT, post_id INTEGER)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS replies 
                  (reply_id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, user_id TEXT, username TEXT, content TEXT, timestamp REAL)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS followers 
                  (follower_id TEXT, following_id TEXT, PRIMARY KEY (follower_id, following_id))''')

# 👥 EXTENSION TABLES: HASHTAG INDEX & CUSTOM USERNAMES
cursor.execute('''CREATE TABLE IF NOT EXISTS user_profiles 
                  (user_id TEXT PRIMARY KEY, custom_username TEXT, bio TEXT DEFAULT 'No bio set.', joined_timestamp REAL)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS hashtags 
                  (tag TEXT, post_id INTEGER, PRIMARY KEY (tag, post_id))''')
db.commit()

# 👥 MASTER ADMIN REGISTRY LIST (Paste numerical IDs inside this bracket list)
ADMIN_USER_IDS = ["968948615726911488", "1211031738839728132"]

# Helper Function: Extract and log hashtags dynamically from post text strings
def index_hashtags(post_id, text):
    tags = re.findall(r"#(\w+)", text.lower())
    for tag in set(tags):
        cursor.execute("INSERT OR IGNORE INTO hashtags (tag, post_id) VALUES (?, ?)", (tag, post_id))
    db.commit()

# Helper Function: Resolve current handles (Falls back to Display Name if no custom username is configured)
def get_kuv_username(user_id, fallback_name):
    cursor.execute("SELECT custom_username FROM user_profiles WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    if res and res[0]:
        return res[0]
    return fallback_name

# 💬 POPUP PANEL FOR WRITING A COMMENT
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

# 🪐 INTERACTIVE ACTIONS LAYOUT
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
        reps = cursor.fetchone()
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
            reply_tree = "*No replies on this post yet. Be the first to drop tea!*"

        thread_embed = discord.Embed(
            title=f"💬 THREAD VIEW • Feed #{self.post_id}",
            description=f"**@{p_user}**: {p_content}",
            color=0xffa500
        )
        thread_embed.add_field(name="─── 💬 REPLIES ───", value=reply_tree, inline=False)
        if img_url:
            thread_embed.set_image(url=img_url)

        await interaction.response.send_message(embed=thread_embed, ephemeral=True)

# 🤖 BASE SYSTEM INITIALIZATION LOGIC
class Bot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True 
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # Bypasses the global sync constraint to force an instant connection
        print("⚡ KUV Advanced Framework Initialized!")

bot = Bot()

# 📝 CORE POSTING ACTION
@bot.tree.command(name="post", description="Post an official update to the KUV Universe!")
async def post(interaction: discord.Interaction, text_content: str, upload_art_url: str = None):
    user_id = str(interaction.user.id)
    username = get_kuv_username(user_id, interaction.user.display_name)
    
    cursor.execute("INSERT INTO posts (user_id, username, content, image_url, likes_count, reposts_count, bot_likes, timestamp) VALUES (?, ?, ?, ?, 0, 0, 0, ?)",
                   (user_id, username, text_content, upload_art_url, time.time()))
    post_id = cursor.lastrowid
    db.commit()
    
    # Process text string to automatically parse tracking tags
    index_hashtags(post_id, text_content)
    await interaction.response.send_message(f"✨ Posted successfully! Assigned Feed ID: #{post_id}.", ephemeral=True)


# 🗑️ DELETE POST TOOL
@bot.tree.command(name="delete_post", description="Delete an active post from the KUV framework.")
async def delete_post(interaction: discord.Interaction, post_id: int):
    user_id = str(interaction.user.id)
    cursor.execute("SELECT user_id FROM posts WHERE post_id = ?", (post_id,))
    res = cursor.fetchone()

    
    if not res:
        return await interaction.response.send_message("❌ This Post ID could not be found.", ephemeral=True)
        
    # Validation: Only the original author or configured network admins can purge records
    if res[0] != user_id and user_id not in ADMIN_USER_IDS:
        return await interaction.response.send_message("❌ You do not have permission to delete this post.", ephemeral=True)
        
    cursor.execute("DELETE FROM posts WHERE post_id = ?", (post_id,))
    cursor.execute("DELETE FROM likes WHERE post_id = ?", (post_id,))
    cursor.execute("DELETE FROM reposts WHERE post_id = ?", (post_id,))
    cursor.execute("DELETE FROM replies WHERE post_id = ?", (post_id,))
    cursor.execute("DELETE FROM hashtags WHERE post_id = ?", (post_id,))
    db.commit()
    
    await interaction.response.send_message(f"✅ Feed ID #{post_id} has been completely wiped from the database.", ephemeral=True)

