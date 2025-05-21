import logging
from config import BOT_TOKEN, API_ID, API_HASH, ADMINS, CHANNELS, DATABASE_URL
from pyrogram import Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler
from pymongo import MongoClient
import asyncio
from datetime import datetime
import secrets
import string
from typing import List, Dict

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize MongoDB
mongo_client = MongoClient(DATABASE_URL)
db = mongo_client["TelegramBot"]
users_col = db["users"]
files_col = db["files"]
channels_col = db["channels"]
requests_col = db["requests"]

# Initialize Pyrogram client
pyro_client = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Constants
FORCE_SUB_IMAGE = "https://envs.sh/gCL.jpg"  # Replace with your image
WELCOME_IMAGE = "https://envs.sh/gCL.jpg"  # Replace with your image

def generate_token(length=16):
    """Generate random token for file sharing"""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

class Bot:
    def __init__(self):
        self.app = Application.builder().token(BOT_TOKEN).build()
        self.setup_handlers()
        
    def setup_handlers(self):
        # Command handlers
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("status", self.status))
        self.app.add_handler(CommandHandler("batch", self.batch))
        self.app.add_handler(CommandHandler("total_req", self.total_requests))
        self.app.add_handler(CommandHandler("del_req", self.delete_requests))
        self.app.add_handler(CommandHandler("set_sub", self.set_subscribe))
        self.app.add_handler(CommandHandler("get_sub", self.get_subscribe))
        self.app.add_handler(CommandHandler("del_sub", self.delete_subscribe))
        self.app.add_handler(CommandHandler("broadcast", self.broadcast))
        
        # File handler
        self.app.add_handler(MessageHandler(
            filters.Document | filters.VIDEO | filters.PHOTO | filters.AUDIO,
            self.handle_file
        ))
        
        # Callback handler
        self.app.add_handler(CallbackQueryHandler(self.button_callback))
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
        # Handle file links
        if context.args and context.args[0].startswith("file_"):
            file_token = context.args[0][5:]
            return await self.handle_file_link(update, context, user, file_token)
        
        # Register user
        await self.register_user(user)
        
        # Check force subscribe
        not_requested = await self.check_join_requests(user.id)
        if not_requested:
            await self.send_force_sub_message(update, user.id, not_requested)
            return
        
        # If all channels requested
        await self.send_welcome_message(update.effective_chat.id)
    
    async def send_welcome_message(self, chat_id):
        """Send welcome image with caption"""
        await self.app.bot.send_photo(
            chat_id=chat_id,
            photo=WELCOME_IMAGE,
            caption="🎉 Welcome! You've successfully requested to join all required channels.\n\n"
                   "Now you can enjoy full access to all bot features!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🌟 Start Exploring", callback_data="start_exploring")]
            ])
        )
    
    async def send_force_sub_message(self, update: Update, user_id: int, channels: List[int]):
        """Send force subscription message with image and buttons"""
        channel_links = []
        for channel in channels:
            try:
                channel_info = await pyro_client.get_chat(channel)
                invite_link = await pyro_client.create_chat_invite_link(
                    channel,
                    creates_join_request=True
                )
                channel_links.append(f"👉 [{channel_info.title}]({invite_link.invite_link})")
            except Exception as e:
                logger.error(f"Error creating join request link: {e}")
                continue
        
        if not channel_links:
            await update.message.reply_text("Error generating join request links. Please try again later.")
            return
        
        # Create the message with image and caption
        caption = (
            "🚀 **Request to Join Our Channels**\n\n"
            "To continue using this bot, please request to join these channels:\n\n"
            + "\n".join(channel_links) + "\n\n"
            "After requesting, click the button below to verify:"
        )
        
        buttons = [
            [InlineKeyboardButton("📢 REQUEST TO JOIN CHANNELS", callback_data="check_requests")],
            [InlineKeyboardButton("✅ I've Requested Access", callback_data="check_requests")]
        ]
        
        # Send photo with caption and buttons
        message = await self.app.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=FORCE_SUB_IMAGE,
            caption=caption,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )
        
        # Store message ID for later deletion
        context.user_data["force_sub_message_id"] = message.message_id
    
    async def register_user(self, user):
        """Register user in database"""
        user_data = {
            "user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "is_bot": user.is_bot,
            "joined_at": datetime.now()
        }
        users_col.update_one({"user_id": user.id}, {"$set": user_data}, upsert=True)
    
    async def check_join_requests(self, user_id: int) -> List[int]:
        """Check which channels user hasn't requested to join"""
        not_requested = []
        for channel in CHANNELS:
            request = requests_col.find_one({"user_id": user_id, "channel_id": channel})
            if not request:
                not_requested.append(channel)
        return not_requested
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        if query.data == "check_requests":
            not_requested = await self.check_join_requests(query.from_user.id)
            if not_requested:
                await query.answer("You haven't requested to join all channels yet!", show_alert=True)
            else:
                # Record all requests
                for channel in CHANNELS:
                    requests_col.update_one(
                        {"user_id": query.from_user.id, "channel_id": channel},
                        {"$set": {"status": "requested", "timestamp": datetime.now()}},
                        upsert=True
                    )
                
                # Delete the force sub message
                try:
                    if "force_sub_message_id" in context.user_data:
                        await self.app.bot.delete_message(
                            chat_id=query.message.chat_id,
                            message_id=context.user_data["force_sub_message_id"]
                        )
                except Exception as e:
                    logger.error(f"Error deleting message: {e}")
                
                # Send welcome message
                await self.send_welcome_message(query.message.chat_id)
        
        elif query.data == "start_exploring":
            await query.edit_message_caption(
                caption="🛠️ Here's what you can do:\n\n"
                      "• Upload files with /upload\n"
                      "• Browse content with /explore\n"
                      "• Get help with /help",
                reply_markup=None
            )
    
    async def handle_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
        # Check force subscribe
        not_requested = await self.check_join_requests(user.id)
        if not_requested:
            await self.send_force_sub_message(update, user.id, not_requested)
            return
        
        # Process the file
        file = None
        file_type = None
        
        if update.message.document:
            file = update.message.document
            file_type = "document"
        elif update.message.video:
            file = update.message.video
            file_type = "video"
        elif update.message.photo:
            file = update.message.photo[-1]
            file_type = "photo"
        elif update.message.audio:
            file = update.message.audio
            file_type = "audio"
        
        if not file:
            await update.message.reply_text("Unsupported file type.")
            return
        
        # Generate unique token
        file_token = generate_token()
        
        # Store file data
        file_data = {
            "file_id": file.file_id,
            "file_unique_id": file.file_unique_id,
            "file_type": file_type,
            "file_name": getattr(file, "file_name", None),
            "file_size": getattr(file, "file_size", None),
            "mime_type": getattr(file, "mime_type", None),
            "caption": update.message.caption,
            "user_id": user.id,
            "timestamp": datetime.now(),
            "token": file_token,
            "access_count": 0
        }
        files_col.insert_one(file_data)
        
        # Create shareable link
        bot_username = (await self.app.bot.get_me()).username
        share_link = f"https://t.me/{bot_username}?start=file_{file_token}"
        
        await update.message.reply_text(
            f"📁 File stored successfully!\n\n"
            f"🔗 Share this link:\n{share_link}\n\n"
            "Recipients will need to request to join channels before accessing.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📤 Share File", url=f"tg://share?url={share_link}")]
            ])
        )
    
    async def handle_file_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user, file_token):
        """Handle file access from shared link"""
        await self.register_user(user)
        
        # Check force subscribe
        not_requested = await self.check_join_requests(user.id)
        if not_requested:
            await self.send_force_sub_message(update, user.id, not_requested)
            return
        
        # Send the file
        file_data = files_col.find_one({"token": file_token})
        if not file_data:
            await update.message.reply_text("File not found or may have been deleted.")
            return
        
        # Update access count
        files_col.update_one(
            {"token": file_token},
            {"$inc": {"access_count": 1}}
        )
        
        # Send based on file type
        file_kwargs = {
            "caption": file_data.get("caption", "Here's your requested file!"),
            "reply_markup": InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Get Shareable Link", callback_data=f"share_{file_token}")]
            ])
        }
        
        if file_data["file_type"] == "document":
            await update.message.reply_document(file_data["file_id"], **file_kwargs)
        elif file_data["file_type"] == "video":
            await update.message.reply_video(file_data["file_id"], **file_kwargs)
        elif file_data["file_type"] == "photo":
            await update.message.reply_photo(file_data["file_id"], **file_kwargs)
        elif file_data["file_type"] == "audio":
            await update.message.reply_audio(file_data["file_id"], **file_kwargs)
    
    # Admin commands
    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMINS:
            await update.message.reply_text("🚫 Admin only command.")
            return
        
        total_users = users_col.count_documents({})
        total_files = files_col.count_documents({})
        total_completed = requests_col.count_documents({"status": "requested"})
        
        text = (
            "📊 Bot Status:\n\n"
            f"👤 Total Users: {total_users}\n"
            f"📂 Total Files: {total_files}\n"
            f"✅ Completed Requests: {total_completed}"
        )
        await update.message.reply_text(text)
    
    async def batch(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMINS:
            await update.message.reply_text("🚫 Admin only command.")
            return
        
        await update.message.reply_text("Batch processing command placeholder")
    
    async def total_requests(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMINS:
            await update.message.reply_text("🚫 Admin only command.")
            return
        
        total = requests_col.count_documents({})
        pending = requests_col.count_documents({"status": {"$exists": False}})
        completed = requests_col.count_documents({"status": "requested"})
        
        text = (
            "📝 Join Requests Statistics:\n\n"
            f"📥 Total Requests: {total}\n"
            f"🔄 Pending Approval: {pending}\n"
            f"✅ Completed: {completed}"
        )
        await update.message.reply_text(text)
    
    async def delete_requests(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMINS:
            await update.message.reply_text("🚫 Admin only command.")
            return
        
        if not context.args:
            await update.message.reply_text("Usage: /del_req <user_id> or /del_req all")
            return
        
        if context.args[0].lower() == "all":
            result = requests_col.delete_many({})
            await update.message.reply_text(f"Deleted {result.deleted_count} requests.")
        else:
            try:
                user_id = int(context.args[0])
                result = requests_col.delete_many({"user_id": user_id})
                await update.message.reply_text(f"Deleted {result.deleted_count} requests for user {user_id}.")
            except ValueError:
                await update.message.reply_text("Invalid user ID.")
    
    async def set_subscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMINS:
            await update.message.reply_text("🚫 Admin only command.")
            return
        
        if not context.args:
            await update.message.reply_text("Usage: /set_sub <channel_id>")
            return
        
        try:
            channel_id = int(context.args[0])
            channels_col.update_one(
                {"channel_id": channel_id},
                {"$set": {"channel_id": channel_id}},
                upsert=True
            )
            global CHANNELS
            if channel_id not in CHANNELS:
                CHANNELS.append(channel_id)
            await update.message.reply_text(f"✅ Channel {channel_id} added to force subscribe.")
        except ValueError:
            await update.message.reply_text("Invalid channel ID.")
    
    async def get_subscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMINS:
            await update.message.reply_text("🚫 Admin only command.")
            return
        
        channels = list(channels_col.find({}, {"_id": 0, "channel_id": 1}))
        if not channels:
            await update.message.reply_text("No force subscribe channels set.")
            return
        
        text = "📢 Force Subscribe Channels:\n\n" + "\n".join(
            f"• {channel['channel_id']}" for channel in channels
        )
        await update.message.reply_text(text)
    
    async def delete_subscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMINS:
            await update.message.reply_text("🚫 Admin only command.")
            return
        
        if not context.args:
            await update.message.reply_text("Usage: /del_sub <channel_id>")
            return
        
        try:
            channel_id = int(context.args[0])
            channels_col.delete_one({"channel_id": channel_id})
            global CHANNELS
            if channel_id in CHANNELS:
                CHANNELS.remove(channel_id)
            await update.message.reply_text(f"✅ Channel {channel_id} removed from force subscribe.")
        except ValueError:
            await update.message.reply_text("Invalid channel ID.")
    
    async def broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMINS:
            await update.message.reply_text("🚫 Admin only command.")
            return
        
        if not context.args:
            await update.message.reply_text("Usage: /broadcast <message>")
            return
        
        message = " ".join(context.args)
        total_users = users_col.count_documents({})
        success = 0
        
        await update.message.reply_text(f"📢 Broadcasting to {total_users} users...")
        
        # Batch processing for speed
        batch_size = 100
        cursor = users_col.find({})
        
        while True:
            batch = []
            try:
                for _ in range(batch_size):
                    batch.append(next(cursor))
            except StopIteration:
                pass
            
            if not batch:
                break
            
            tasks = []
            for user in batch:
                try:
                    tasks.append(
                        context.bot.send_message(
                            chat_id=user["user_id"],
                            text=message
                        )
                    )
                except Exception as e:
                    logger.error(f"Error sending to {user['user_id']}: {e}")
            
            # Send concurrently
            results = await asyncio.gather(*tasks, return_exceptions=True)
            success += sum(1 for r in results if not isinstance(r, Exception))
            
            # Small delay to prevent flooding
            await asyncio.sleep(0.1)
        
        await update.message.reply_text(
            f"📢 Broadcast complete!\n"
            f"✅ Success: {success}\n"
            f"❌ Failed: {total_users - success}"
        )
    
    def run(self):
        # Start Pyrogram client
        pyro_client.start()
        
        # Start the bot
        self.app.run_polling(allowed_updates=Update.ALL_TYPES)
        
        # Stop Pyrogram client when bot stops
        pyro_client.stop()

if __name__ == "__main__":
    bot = Bot()
    bot.run()
