import discord
from discord import app_commands
import sqlite3
import time
import os
import threading
from http.server import SimpleHTTPRequestHandler, HTTPServer

# 💾 CONNECT TO SOCIAL ALGORITHM DATABASE
db = sqlite3.connect('kuv_social_media.db')
cursor = db.cursor()

# Create structured data tables
cursor.execute('''CREATE TABLE IF NOT EXISTS posts 
                  (post_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, username TEXT, content TEXT, image_url TEXT, likes_count INTEGER, reposts_count INTEGER, bot_likes INTEGER DEFAULT 0, timestamp REAL)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS likes (user_id TEXT, post_id INTEGER)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS reposts (user_id TEXT, post_id INTEGER)''')

# 💬 NEW DATABASE TABLE FOR TWITTER-STYLE REPLIES
cursor.execute('''CREATE TABLE IF NOT EXISTS replies 
                  (reply_id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, user_id TEXT, username TEXT, content TEXT, timestamp REAL)''')
db.commit()

# YOUR DISCORD USER ID HERE
ADMIN_USER_ID = "YOUR_PERSONAL_DISCORD_USER_ID"

# 💬 INTERACTIVE POPUP MODAL FOR TYPING A REPLY
class ReplyModal(discord.ui.Modal, title="💬 Drop Your Tea / Reply"):
    reply_text = discord.ui.TextInput(
        label="What's the word on the netz?",
        style=discord.TextStyle.paragraph,
        placeholder="Type your reply or gossip commentary here...",
        required=True,
        max_length=500
    )

    def __init__(self, post_id):
        super().__init__()
        self.post_id = post_id

    async def on_submit(self, interaction: discord.Interaction):
        username = interaction.user.display_name
        # Save comment rows securely into the new table
        cursor.execute("INSERT INTO replies (post_id, user_id, username, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                       (self.post_id, str(interaction.user.id), username, self.reply_text.value, time.time()))
        db.commit()
        await interaction.response.send_message(f"🤫 Your reply has been indexed onto Feed #{self.post_id}!", ephemeral=True)

class SocialFeedButtons(discord.ui.View):
    def __init__(self, post_id):
        super().__init__(timeout=None)
        self.post_id = post_id

    @discord.ui.button(label="💖 Like (0)", style=discord.ButtonStyle.grey, custom_id="social_like_btn")
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

    @discord.ui.button(label="🔁 Repost (0)", style=discord.ButtonStyle.grey, custom_id="social_repost_btn")
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

    # 💬 NEW CLICKABLE REPLY TRIGGER BUTTON
    @discord.ui.button(label="💬 Reply", style=discord.ButtonStyle.blurple, custom_id="social_reply_btn")
    async def reply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Open the pop-up text field box for typing the tea
        await interaction.response.send_modal(ReplyModal(self.post_id))

# 🤖 CLEAN GLOBAL BOT INITIALIZATION ENGINE
class Bot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True 
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        print("⚡ KUV Social Media has successfully synchronized all global command paths!")

bot = Bot()

@bot.tree.command(name="post", description="Post an official update to the KUV Universe!")
async def post(interaction: discord.Interaction, text_content: str, upload_art_url: str = None):
    username = interaction.user.display_name
    cursor.execute("INSERT INTO posts (user_id, username, content, image_url, likes_count, reposts_count, bot_likes, timestamp) VALUES (?, ?, ?, ?, 0, 0, 0, ?)",
                   (str(interaction.user.id), username, text_content, upload_art_url, time.time()))
    db.commit()
    await interaction.response.send_message("✨ Posted successfully! Your post is now entering the KUV Algorithm.", ephemeral=True)

@bot.tree.command(name="fyp", description="View the top trending posts on the KUV algorithm!")
async def fyp(interaction: discord.Interaction):
    # Fixes the 3-second timeout constraint instantly
    await interaction.response.defer()
    
    cursor.execute("SELECT post_id, username, content, image_url, likes_count, reposts_count, bot_likes FROM posts ORDER BY (likes_count + bot_likes + (reposts_count * 2)) DESC LIMIT 5")
    trending_posts = cursor.fetchall()
    
    if not trending_posts:
        return await interaction.followup.send("🦋 The FYP is empty right now. Be the first to use `/post`!", ephemeral=True)
        
    for row in trending_posts:
        post_id, username, content, img_url, likes, reps, b_likes = row
        total_likes = likes + b_likes
        
        # Gather all replies registered to this post thread from the database
        cursor.execute("SELECT username, content FROM replies WHERE post_id = ? ORDER BY timestamp ASC", (post_id,))
        all_replies = cursor.fetchall()
        
        # Build the dynamic thread visualization string
        thread_text = content
        if all_replies:
            thread_text += "\n\n─── 💬 NETZ THREAD ───"
            for r_user, r_content in all_replies:
                thread_text += f"\n**@{r_user}**: {r_content}"
        
        embed = discord.Embed(
            title=f"🔥 TRENDING ON KUV • {username}",
            description=thread_text,
            color=0xffa500
        )
        embed.set_footer(text=f"Feed ID: #{post_id} • 💖 {total_likes} Likes • 🔁 {reps} Reposts")
        if img_url:
            embed.set_image(url=img_url)
            
        view = SocialFeedButtons(post_id)
        # Uses followup tracking token to permanently clear the "Did Not Respond" error
        await interaction.followup.send(embed=embed, view=view)

@bot.tree.command(name="add_likes", description="Admin Only: Inject bot likes onto a trending post!")
async def add_likes(interaction: discord.Interaction, post_id: int, amount: int):
    if str(interaction.user.id) != ADMIN_USER_ID:
        return await interaction.response.send_message("❌ You do not have permission to manipulate the KUV algorithm.", ephemeral=True)
        
    cursor.execute("SELECT username FROM posts WHERE post_id = ?", (post_id,))
    post_exists = cursor.fetchone()
    
    if not post_exists:
        return await interaction.response.send_message(f"❌ Post ID #{post_id} could not be found.", ephemeral=True)
        
    cursor.execute("UPDATE posts SET bot_likes = bot_likes + ? WHERE post_id = ?", (amount, post_id))
    db.commit()
    
    await interaction.response.send_message(f"🚀 Success! Injected **{amount} Bot Likes** onto Post #{post_id}.", ephemeral=True)

# 🌐 INTERNAL WEB SERVER FOR RENDER HEALTH CHECKS
def run_web_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    print(f"⚓ Health check server listening on port {port}")
    server.serve_forever()

# Start the web server in a background thread so it doesn't block the bot
threading.Thread(target=run_web_server, daemon=True).start()

# Pulls your secure token from your Environment variable vault safely
bot.run(os.getenv("TOKEN_PLACEHOLDER"))
