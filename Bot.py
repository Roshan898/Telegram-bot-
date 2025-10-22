import sqlite3
import logging
import requests
from datetime import datetime, timedelta
from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup
)
from telegram.ext import (
    Application, 
    CommandHandler, 
    ContextTypes, 
    CallbackQueryHandler,
    MessageHandler,
    filters
)

# Bot Configuration
BOT_TOKEN = "8207532706:AAHsU7eMzwNJxRUHhi14XGsc9OpG6QH-h0U"
ADMIN_ID = "6567632240"  # Your Telegram user ID
ADMIN_CHANNEL = "@heyjshs"  # Channel for transaction notifications

# Database setup
DB_NAME = "crypto_swap_bot.db"

# Crypto prices API (using CoinGecko)
CRYPTO_PRICES = {
    "USDT": 83.0,  # Default price, will be updated from API
    "BTC": 3500000.0,
    "ETH": 250000.0
}

# Wallet addresses for different blockchains
WALLETS = {
    "USDT": {
        "TRC20": "C6JPAswJarBCWrsjAWMsEvB4hrNcKG1DBcGjPCBfPY4o",
        "TRON": "TXJgC8AMDWifSho1jRZAurWSprLEYsFMtP",
        "BNB smart chain": "0x334A76871A0FaA559B1b2183679C4A00cd728557"
    },
    "BTC": {
        "BTC": "bc1qgfh09k3u0w9lsy4w9ln34z4850jruc49u7qjr6"
    },
    "ETH": {
        "ETH": "0x4d20892695634a00fcb00100c065da914c99ce7d"
    }
}

# Payment methods
PAYMENT_METHODS = ["UPI", "Bank Transfer", "Paytm", "Google Pay"]

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

class CryptoSwapBot:
    def __init__(self):
        self.init_db()
        self.update_crypto_prices()
    
    def update_crypto_prices(self):
        """Update crypto prices from CoinGecko API"""
        try:
            response = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={
                    "ids": "tether,bitcoin,ethereum",
                    "vs_currencies": "inr"
                },
                timeout=10
            )
            data = response.json()
            
            CRYPTO_PRICES["USDT"] = data.get("tether", {}).get("inr", 83.0)
            CRYPTO_PRICES["BTC"] = data.get("bitcoin", {}).get("inr", 3500000.0)
            CRYPTO_PRICES["ETH"] = data.get("ethereum", {}).get("inr", 250000.0)
            
            logging.info(f"Updated crypto prices: {CRYPTO_PRICES}")
        except Exception as e:
            logging.error(f"Failed to update crypto prices: {e}")
    
    def get_crypto_price(self, crypto_type):
        """Get current price of cryptocurrency in INR"""
        return CRYPTO_PRICES.get(crypto_type, 1.0)
    
    def calculate_inr_amount(self, crypto_type, crypto_amount):
        """Calculate INR amount based on crypto amount"""
        price = self.get_crypto_price(crypto_type)
        return crypto_amount * price
    
    def init_db(self):
        """Initialize SQLite database with required tables"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                referral_code TEXT UNIQUE,
                referred_by INTEGER,
                referral_balance REAL DEFAULT 0,
                total_earned REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Orders table - MODIFIED: Added 'temp_order' status
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                crypto_type TEXT,
                crypto_amount REAL,
                inr_amount REAL,
                payment_method TEXT,
                payment_details TEXT,
                blockchain TEXT,
                fee REAL,
                net_amount REAL,
                status TEXT DEFAULT 'temp_order',
                transaction_link TEXT,
                wallet_address TEXT,
                expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # Referral transactions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS referral_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                order_id INTEGER,
                amount REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (referrer_id) REFERENCES users (user_id),
                FOREIGN KEY (order_id) REFERENCES orders (order_id)
            )
        ''')
        
        # Withdrawal requests table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS withdrawal_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def get_user(self, user_id):
        """Get user from database or create if not exists"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        user = cursor.fetchone()
        
        if not user:
            # Generate referral code
            referral_code = f"REF{user_id}{datetime.now().strftime('%H%M%S')}"
            cursor.execute('''
                INSERT INTO users (user_id, referral_code) 
                VALUES (?, ?)
            ''', (user_id, referral_code))
            conn.commit()
            
            cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            user = cursor.fetchone()
        
        conn.close()
        return user
    
    def create_temp_order(self, user_id, crypto_type, crypto_amount, payment_method, payment_details, blockchain):
        """Create a temporary order that will be confirmed only after transaction link submission"""
        # Calculate the fee in crypto (3%)
        crypto_fee = crypto_amount * 0.03
        net_crypto_amount = crypto_amount - crypto_fee
        
        # Calculate INR amount based on net crypto (what user actually sends)
        inr_amount = self.calculate_inr_amount(crypto_type, net_crypto_amount)
        
        # No additional ₹100 fee, only the 3% crypto fee
        fee = crypto_fee * self.get_crypto_price(crypto_type)  # Fee value in INR for display
        net_amount = inr_amount  # User receives full INR value of net crypto
        
        expires_at = datetime.now() + timedelta(minutes=15)
        wallet_address = WALLETS.get(crypto_type, {}).get(blockchain, "Address not configured")
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO orders 
            (user_id, crypto_type, crypto_amount, inr_amount, payment_method, payment_details, blockchain, fee, net_amount, wallet_address, expires_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'temp_order')
        ''', (user_id, crypto_type, crypto_amount, inr_amount, payment_method, payment_details, blockchain, fee, net_amount, wallet_address, expires_at))
        
        order_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {
            'order_id': order_id,
            'inr_amount': inr_amount,
            'fee': fee,
            'net_amount': net_amount,
            'wallet_address': wallet_address,
            'expires_at': expires_at,
            'crypto_fee': crypto_fee,
            'net_crypto_amount': net_crypto_amount
        }
    
    def confirm_order_with_transaction(self, order_id, transaction_link):
        """Confirm the order by updating status and adding transaction link"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE orders 
            SET status = 'pending', transaction_link = ? 
            WHERE order_id = ? AND status = 'temp_order'
        ''', (transaction_link, order_id))
        
        rows_affected = cursor.rowcount
        
        if rows_affected > 0:
            # Process referral earnings if applicable
            cursor.execute('SELECT user_id, fee FROM orders WHERE order_id = ?', (order_id,))
            order_data = cursor.fetchone()
            if order_data:
                user_id, fee = order_data
                user = self.get_user(user_id)
                if user[4]:  # if referred_by exists
                    referral_earning = fee * 0.20  # 20% of fee
                    self.add_referral_earning(user[4], order_id, referral_earning)
        
        conn.commit()
        conn.close()
        return rows_affected > 0
    
    def update_order_status(self, order_id, status, transaction_link=None):
        """Update order status"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if transaction_link:
            cursor.execute('''
                UPDATE orders SET status = ?, transaction_link = ? WHERE order_id = ?
            ''', (status, transaction_link, order_id))
        else:
            cursor.execute('''
                UPDATE orders SET status = ? WHERE order_id = ?
            ''', (status, order_id))
        
        conn.commit()
        conn.close()
    
    def add_referral_earning(self, referrer_id, order_id, amount):
        """Add referral earnings"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO referral_transactions (referrer_id, order_id, amount)
            VALUES (?, ?, ?)
        ''', (referrer_id, order_id, amount))
        
        cursor.execute('''
            UPDATE users 
            SET referral_balance = referral_balance + ?, total_earned = total_earned + ?
            WHERE user_id = ?
        ''', (amount, amount, referrer_id))
        
        conn.commit()
        conn.close()
    
    def get_order_details(self, order_id):
        """Get complete order details"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT o.*, u.username, u.first_name 
            FROM orders o 
            LEFT JOIN users u ON o.user_id = u.user_id 
            WHERE o.order_id = ?
        ''', (order_id,))
        order = cursor.fetchone()
        
        conn.close()
        return order
    
    def get_pending_orders(self):
        """Get all pending orders (excluding temp orders)"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT o.*, u.username, u.first_name 
            FROM orders o 
            LEFT JOIN users u ON o.user_id = u.user_id 
            WHERE o.status = 'pending'
            ORDER BY o.created_at DESC
        ''')
        orders = cursor.fetchall()
        
        conn.close()
        return orders
    
    def get_pending_withdrawals(self):
        """Get pending withdrawal requests"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT w.*, u.username, u.first_name, u.referral_balance
            FROM withdrawal_requests w 
            LEFT JOIN users u ON w.user_id = u.user_id 
            WHERE w.status = 'pending'
            ORDER BY w.created_at DESC
        ''')
        withdrawals = cursor.fetchall()
        
        conn.close()
        return withdrawals
    
    def get_admin_stats(self):
        """Get admin statistics"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # Total orders
        cursor.execute('SELECT COUNT(*) FROM orders')
        total_orders = cursor.fetchone()[0]
        
        # Completed orders
        cursor.execute('SELECT COUNT(*) FROM orders WHERE status = "completed"')
        completed_orders = cursor.fetchone()[0]
        
        # Total traded amount
        cursor.execute('SELECT COALESCE(SUM(inr_amount), 0) FROM orders WHERE status = "completed"')
        total_traded = cursor.fetchone()[0]
        
        # Total fees collected
        cursor.execute('SELECT COALESCE(SUM(fee), 0) FROM orders WHERE status = "completed"')
        total_fees = cursor.fetchone()[0]
        
        # Total users
        cursor.execute('SELECT COUNT(*) FROM users')
        total_users = cursor.fetchone()[0]
        
        # Pending withdrawals
        cursor.execute('SELECT COUNT(*) FROM withdrawal_requests WHERE status = "pending"')
        pending_withdrawals = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_orders': total_orders,
            'completed_orders': completed_orders,
            'total_traded': total_traded,
            'total_fees': total_fees,
            'total_users': total_users,
            'pending_withdrawals': pending_withdrawals
        }
    
    def update_withdrawal_status(self, withdrawal_id, status):
        """Update withdrawal request status"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE withdrawal_requests SET status = ? WHERE id = ?
        ''', (status, withdrawal_id))
        
        conn.commit()
        conn.close()
    
    def search_orders(self, search_term):
        """Search orders by order ID or user ID"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        try:
            # Try to search by order ID
            order_id = int(search_term)
            cursor.execute('''
                SELECT o.*, u.username, u.first_name 
                FROM orders o 
                LEFT JOIN users u ON o.user_id = u.user_id 
                WHERE o.order_id = ?
            ''', (order_id,))
        except ValueError:
            # Search by user ID
            cursor.execute('''
                SELECT o.*, u.username, u.first_name 
                FROM orders o 
                LEFT JOIN users u ON o.user_id = u.user_id 
                WHERE o.user_id = ? OR u.username LIKE ? OR u.first_name LIKE ?
            ''', (search_term, f'%{search_term}%', f'%{search_term}%'))
        
        orders = cursor.fetchall()
        conn.close()
        return orders

    def cleanup_temp_orders(self):
        """Clean up temporary orders that expired without transaction link"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            DELETE FROM orders 
            WHERE status = 'temp_order' AND expires_at < ?
        ''', (datetime.now(),))
        
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        if deleted_count > 0:
            logging.info(f"Cleaned up {deleted_count} expired temporary orders")
        
        return deleted_count

# Initialize bot
bot = CryptoSwapBot()

# ==================== ORDER TRACKING FUNCTIONS ====================

async def view_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View user's order history"""
    user_id = update.effective_user.id
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT order_id, crypto_type, crypto_amount, inr_amount, status, created_at 
        FROM orders 
        WHERE user_id = ? AND status != 'temp_order'
        ORDER BY created_at DESC 
        LIMIT 10
    ''', (user_id,))
    
    orders = cursor.fetchall()
    conn.close()
    
    if not orders:
        await update.message.reply_text(
            "📭 You don't have any orders yet.\n\n"
            "Use /swap to start your first swap!",
            parse_mode='Markdown'
        )
        return
    
    orders_text = "📋 **Your Recent Orders**\n\n"
    
    for order in orders:
        order_id, crypto_type, crypto_amount, inr_amount, status, created_at = order
        
        status_icons = {
            'pending': '⏳',
            'completed': '✅',
            'rejected': '❌',
            'temp_order': '📝'
        }
        
        status_display = {
            'pending': 'Pending Review',
            'completed': 'Completed',
            'rejected': 'Rejected',
            'temp_order': 'Draft'
        }
        
        orders_text += f"{status_icons.get(status, '📄')} **Order #{order_id}**\n"
        orders_text += f"• Crypto: {crypto_amount} {crypto_type}\n"
        orders_text += f"• Amount: ₹{inr_amount:.2f}\n"
        orders_text += f"• Status: {status_display.get(status, status)}\n"
        orders_text += f"• Date: {created_at}\n"
        orders_text += f"• Details: /order_{order_id}\n\n"
    
    if len(orders) == 10:
        orders_text += "📄 Showing last 10 orders. Use /order_<ID> to view specific order details."
    
    keyboard = [
        [InlineKeyboardButton("🔄 Start New Swap", callback_data="start_swap")],
        [InlineKeyboardButton("📊 My Stats", callback_data="my_stats")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(orders_text, reply_markup=reply_markup, parse_mode='Markdown')

async def view_single_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View specific order details"""
    user_id = update.effective_user.id
    command_text = update.message.text
    
    # Extract order ID from command
    try:
        if command_text.startswith('/order_'):
            order_id = int(command_text.split('_')[1])
        else:
            await update.message.reply_text(
                "❌ Invalid format. Use: `/order_123` or /orders",
                parse_mode='Markdown'
            )
            return
    except (IndexError, ValueError):
        await update.message.reply_text(
            "❌ Invalid order ID. Use: `/order_123`",
            parse_mode='Markdown'
        )
        return
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM orders 
        WHERE order_id = ? AND user_id = ? AND status != 'temp_order'
    ''', (order_id, user_id))
    
    order = cursor.fetchone()
    conn.close()
    
    if not order:
        await update.message.reply_text(
            f"❌ Order #{order_id} not found or you don't have permission to view it.",
            parse_mode='Markdown'
        )
        return
    
    await send_order_details(update, context, order, is_admin=False)

async def send_order_details(update: Update, context: ContextTypes.DEFAULT_TYPE, order: tuple, is_admin: bool = False):
    """Send order details in a formatted message"""
    order_id, user_id, crypto_type, crypto_amount, inr_amount, payment_method, \
    payment_details, blockchain, fee, net_amount, status, transaction_link, \
    wallet_address, expires_at, created_at = order
    
    status_icons = {
        'pending': '⏳',
        'completed': '✅',
        'rejected': '❌'
    }
    
    status_display = {
        'pending': '⏳ Pending Admin Review',
        'completed': '✅ Completed',
        'rejected': '❌ Rejected'
    }
    
    order_text = f"""
{status_icons.get(status, '📄')} **Order #{order_id}**

💰 **Transaction Details:**
• Cryptocurrency: {crypto_type}
• Crypto Amount: {crypto_amount}
• INR Value: ₹{inr_amount:.2f}
• Blockchain: {blockchain}
• Wallet Used: `{wallet_address}`

💸 **Financial Breakdown:**
• Transaction Fee: ₹{fee:.2f}
• Net Amount Received: ₹{net_amount:.2f}

🏦 **Payment Information:**
• Payment Method: {payment_method}
• Your Details: `{payment_details}`

📊 **Status:** {status_display.get(status, status)}
    
🕒 **Order Created:** {created_at}
"""
    
    if transaction_link:
        order_text += f"🔗 **Transaction Proof:** {transaction_link}\n"
    
    if status == 'pending':
        order_text += "\n⏳ **Admin is reviewing your transaction. You'll be notified once processed.**"
    elif status == 'completed':
        order_text += f"\n✅ **Payment of ₹{net_amount:.2f} has been sent to your {payment_method} account.**"
    elif status == 'rejected':
        order_text += "\n❌ **This order was rejected. Contact support @ROSHAN_86 for assistance.**"
    
    keyboard = []
    if is_admin and status == 'pending':
        keyboard.append([
            InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve_{order_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject_{order_id}")
        ])
    
    keyboard.append([InlineKeyboardButton("📋 View All Orders", callback_data="view_orders")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if isinstance(update, Update) and update.message:
        await update.message.reply_text(order_text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.callback_query.edit_message_text(order_text, reply_markup=reply_markup, parse_mode='Markdown')

async def view_orders_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback for viewing orders"""
    query = update.callback_query
    user_id = query.from_user.id
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT order_id, crypto_type, crypto_amount, inr_amount, status, created_at 
        FROM orders 
        WHERE user_id = ? AND status != 'temp_order'
        ORDER BY created_at DESC 
        LIMIT 5
    ''', (user_id,))
    
    orders = cursor.fetchall()
    conn.close()
    
    if not orders:
        await query.edit_message_text(
            "📭 You don't have any orders yet.\n\n"
            "Click below to start your first swap!",
            parse_mode='Markdown'
        )
        return
    
    orders_text = "📋 **Your Recent Orders**\n\n"
    
    for order in orders:
        order_id, crypto_type, crypto_amount, inr_amount, status, created_at = order
        
        status_icons = {
            'pending': '⏳',
            'completed': '✅',
            'rejected': '❌'
        }
        
        orders_text += f"{status_icons.get(status, '📄')} **Order #{order_id}**\n"
        orders_text += f"• {crypto_amount} {crypto_type} → ₹{inr_amount:.2f}\n"
        orders_text += f"• Status: {status.title()}\n"
        orders_text += f"• Date: {created_at[:16]}\n\n"
    
    keyboard = []
    for order in orders:
        order_id = order[0]
        keyboard.append([InlineKeyboardButton(f"📄 Order #{order_id}", callback_data=f"user_order_{order_id}")])
    
    keyboard.append([InlineKeyboardButton("🔄 Start New Swap", callback_data="start_swap")])
    keyboard.append([InlineKeyboardButton("📊 My Stats", callback_data="my_stats")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(orders_text, reply_markup=reply_markup, parse_mode='Markdown')

async def view_user_order_details(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int):
    """View specific order details from callback"""
    query = update.callback_query
    user_id = query.from_user.id
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM orders 
        WHERE order_id = ? AND user_id = ? AND status != 'temp_order'
    ''', (order_id, user_id))
    
    order = cursor.fetchone()
    conn.close()
    
    if not order:
        await query.answer("Order not found!", show_alert=True)
        return
    
    await send_order_details_callback(update, context, order)

async def send_order_details_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, order: tuple):
    """Send order details for callback queries"""
    query = update.callback_query
    order_id, user_id, crypto_type, crypto_amount, inr_amount, payment_method, \
    payment_details, blockchain, fee, net_amount, status, transaction_link, \
    wallet_address, expires_at, created_at = order
    
    status_icons = {
        'pending': '⏳',
        'completed': '✅',
        'rejected': '❌'
    }
    
    status_display = {
        'pending': '⏳ Pending Admin Review',
        'completed': '✅ Completed',
        'rejected': '❌ Rejected'
    }
    
    order_text = f"""
{status_icons.get(status, '📄')} **Order #{order_id}**

💰 **Transaction Details:**
• Cryptocurrency: {crypto_type}
• Crypto Amount: {crypto_amount}
• INR Value: ₹{inr_amount:.2f}
• Blockchain: {blockchain}
• Wallet Used: `{wallet_address}`

💸 **Financial Breakdown:**
• Transaction Fee: ₹{fee:.2f}
• Net Amount Received: ₹{net_amount:.2f}

🏦 **Payment Information:**
• Payment Method: {payment_method}
• Your Details: `{payment_details}`

📊 **Status:** {status_display.get(status, status)}
    
🕒 **Order Created:** {created_at}
"""
    
    if transaction_link:
        order_text += f"🔗 **Transaction Proof:** {transaction_link}\n"
    
    if status == 'pending':
        order_text += "\n⏳ **Admin is reviewing your transaction. You'll be notified once processed.**"
    elif status == 'completed':
        order_text += f"\n✅ **Payment of ₹{net_amount:.2f} has been sent to your {payment_method} account.**"
    elif status == 'rejected':
        order_text += "\n❌ **This order was rejected. Contact support @ROSHAN_86 for assistance.**"
    
    keyboard = [
        [InlineKeyboardButton("📋 Back to Orders", callback_data="view_orders")],
        [InlineKeyboardButton("🔄 Start New Swap", callback_data="start_swap")],
        [InlineKeyboardButton("🆘 Contact Support", url="https://t.me/ROSHAN_86")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(order_text, reply_markup=reply_markup, parse_mode='Markdown')

async def admin_user_orders(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Admin view of all user's orders"""
    query = update.callback_query
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT order_id, crypto_type, crypto_amount, inr_amount, status, created_at 
        FROM orders 
        WHERE user_id = ? AND status != 'temp_order'
        ORDER BY created_at DESC
    ''', (user_id,))
    
    orders = cursor.fetchall()
    
    cursor.execute('SELECT first_name, username FROM users WHERE user_id = ?', (user_id,))
    user_info = cursor.fetchone()
    conn.close()
    
    user_name = user_info[0] or 'N/A'
    username = f"@{user_info[1]}" if user_info[1] else 'No username'
    
    if not orders:
        text = f"👤 **User Orders: {user_name}** ({username})\n\n📭 No orders found for this user."
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        return
    
    text = f"👤 **User Orders: {user_name}** ({username})\n\n"
    
    for order in orders[:10]:
        order_id, crypto_type, crypto_amount, inr_amount, status, created_at = order
        
        status_icons = {
            'pending': '⏳',
            'completed': '✅',
            'rejected': '❌'
        }
        
        text += f"{status_icons.get(status, '📄')} **Order #{order_id}**\n"
        text += f"• {crypto_amount} {crypto_type} → ₹{inr_amount:.2f}\n"
        text += f"• Status: {status.title()}\n"
        text += f"• Date: {created_at[:16]}\n\n"
    
    if len(orders) > 10:
        text += f"📄 Showing 10 of {len(orders)} orders\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

# ==================== EXISTING BOT FUNCTIONS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    # Register user
    bot.get_user(user_id)
    
    # Check if user was referred or viewing order
    if context.args:
        referral_code = context.args[0]
        
        # Check if it's an order view request
        if referral_code.startswith('order_'):
            try:
                order_id = int(referral_code.split('_')[1])
                # Create a fake message to trigger order view
                update.message.text = f"/order_{order_id}"
                await view_single_order(update, context)
                return
            except (IndexError, ValueError):
                pass
        
        # Handle referral code
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM users WHERE referral_code = ?', (referral_code,))
        referrer = cursor.fetchone()
        if referrer and referrer[0] != user_id:
            cursor.execute('UPDATE users SET referred_by = ? WHERE user_id = ?', (referrer[0], user_id))
            conn.commit()
        conn.close()
    
    welcome_text = f"""
🚀 **Welcome to Crypto Swap Bot!** 🚀

Easily swap your cryptocurrencies (USDT, BTC, ETH) for INR directly on Telegram!

**Features:**
• Fast & Secure P2P Swaps
• Multiple Payment Methods
• Referral Rewards (20% of fees)
• 24/7 Support
• Order Tracking

**Quick Commands:**
/swap - Start new swap
/orders - View your orders
/myref - Referral program
/support - Get help

Click /swap to start swapping!
    """
    
    keyboard = [
        [InlineKeyboardButton("🔄 Start Swap", callback_data="start_swap")],
        [InlineKeyboardButton("📋 My Orders", callback_data="view_orders"),
         InlineKeyboardButton("📊 My Stats", callback_data="my_stats")],
        [InlineKeyboardButton("👥 Referrals", callback_data="referrals"),
         InlineKeyboardButton("🆘 Support", callback_data="support")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

async def swap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("USDT", callback_data="crypto_USDT"),
         InlineKeyboardButton("BTC", callback_data="crypto_BTC")],
        [InlineKeyboardButton("ETH", callback_data="crypto_ETH")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "💰 **Select Cryptocurrency:**",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    support_text = """
🆘 **Support Center**

For any issues or queries:
• Contact Support: @ROSHAN_86
• Email: support@yourdomain.com
• Response Time: 2-4 hours

Common Issues:
• Transaction delays? Wait 15-30 minutes
• Wrong network? Contact support immediately
• Payment issues? Provide transaction proof
    """
    await update.message.reply_text(support_text, parse_mode='Markdown')

async def myref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = bot.get_user(user_id)
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Count referrals
    cursor.execute('SELECT COUNT(*) FROM users WHERE referred_by = ?', (user_id,))
    referral_count = cursor.fetchone()[0]
    
    conn.close()
    
    ref_text = f"""
👥 **Your Referral Stats**

🔗 Your Referral Link:
`https://t.me/your_bot_username?start={user[3]}`

📊 Stats:
• Total Referrals: {referral_count}
• Current Balance: ₹{user[5]:.2f}
• Total Earned: ₹{user[6]:.2f}

💡 **How it works:**
Earn 20% of transaction fees from every swap made by your referrals!
    """
    
    keyboard = [
        [InlineKeyboardButton("💰 Withdraw Earnings", callback_data="withdraw_ref")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(ref_text, reply_markup=reply_markup, parse_mode='Markdown')

# Callback query handlers
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data == "start_swap":
        await swap_callback(update, context)
    elif data.startswith("crypto_"):
        crypto_type = data.split("_")[1]
        context.user_data['crypto_type'] = crypto_type
        await ask_amount(update, context, crypto_type)
    elif data.startswith("payment_"):
        payment_method = data.split("_")[1]
        context.user_data['payment_method'] = payment_method
        await ask_payment_details(update, context, payment_method)
    elif data.startswith("blockchain_"):
        blockchain = data.split("_")[1]
        await process_swap(update, context, blockchain)
    elif data.startswith("submit_tx_"):
        order_id = int(data.split("_")[2])
        context.user_data['current_order'] = order_id
        await ask_transaction_link(update, context)
    elif data.startswith("cancel_order_"):
        order_id = int(data.split("_")[2])
        await cancel_temp_order(update, context, order_id)
    elif data == "my_stats":
        await show_stats(update, context)
    elif data == "referrals":
        await myref_callback(update, context)
    elif data == "support":
        await support_callback(update, context)
    elif data == "withdraw_ref":
        await withdraw_ref(update, context)
    
    # Order tracking callbacks
    elif data == "view_orders":
        await view_orders_callback(update, context)
    elif data.startswith("user_order_"):
        order_id = int(data.split("_")[2])
        await view_user_order_details(update, context, order_id)
    
    # Admin callbacks
    elif data.startswith("admin_"):
        if str(user_id) != ADMIN_ID:
            await query.answer("❌ Access denied!", show_alert=True)
            return
        await handle_admin_callback(update, context, data)

async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query
    
    if data == "admin_stats":
        await admin_stats(update, context)
    elif data == "admin_orders":
        await admin_pending_orders(update, context)
    elif data == "admin_withdrawals":
        await admin_pending_withdrawals(update, context)
    elif data == "admin_wallets":
        await admin_wallet_settings(update, context)
    elif data == "admin_search":
        await admin_search_order(update, context)
    elif data == "admin_back":
        await admin_panel_callback(update, context)
    elif data.startswith("admin_approve_"):
        order_id = int(data.split("_")[2])
        await admin_approve_order(update, context, order_id)
    elif data.startswith("admin_reject_"):
        order_id = int(data.split("_")[2])
        await admin_reject_order(update, context, order_id)
    elif data.startswith("admin_approve_wd_"):
        wd_id = int(data.split("_")[3])
        await admin_approve_withdrawal(update, context, wd_id)
    elif data.startswith("admin_reject_wd_"):
        wd_id = int(data.split("_")[3])
        await admin_reject_withdrawal(update, context, wd_id)
    elif data.startswith("admin_view_"):
        order_id = int(data.split("_")[2])
        await admin_view_order(update, context, order_id)
    elif data.startswith("admin_user_orders_"):
        user_id = int(data.split("_")[3])
        await admin_user_orders(update, context, user_id)

async def swap_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    keyboard = [
        [InlineKeyboardButton("USDT", callback_data="crypto_USDT"),
         InlineKeyboardButton("BTC", callback_data="crypto_BTC")],
        [InlineKeyboardButton("ETH", callback_data="crypto_ETH")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "💰 **Select Cryptocurrency:**",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def ask_amount(update: Update, context: ContextTypes.DEFAULT_TYPE, crypto_type: str):
    query = update.callback_query
    
    # Get current price
    price = bot.get_crypto_price(crypto_type)
    
    await query.edit_message_text(
        f"💵 **Enter {crypto_type} Amount to Swap:**\n\n"
        f"Current Price: ₹{price:.2f} per {crypto_type}\n"
        f"Fee: 3% (deducted from crypto amount)\n\n"
        "Example: `100` or `0.5`\n\n"
        "Note: You'll send the amount after 3% fee deduction.",
        parse_mode='Markdown'
    )
    context.user_data['waiting_for_amount'] = True

async def ask_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    for method in PAYMENT_METHODS:
        keyboard.append([InlineKeyboardButton(method, callback_data=f"payment_{method}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🏦 **Select Payment Method to Receive INR:**",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def ask_payment_details(update: Update, context: ContextTypes.DEFAULT_TYPE, payment_method: str):
    query = update.callback_query
    
    if payment_method == "UPI":
        instruction = "Please enter your UPI ID:"
        example = "Example: `yourname@upi`"
    elif payment_method == "Bank Transfer":
        instruction = "Please enter your bank details (Account Number, IFSC, Account Name):"
        example = "Example: `1234567890, SBIN0000001, John Doe`"
    elif payment_method in ["Paytm", "Google Pay"]:
        instruction = f"Please enter your {payment_method} number:"
        example = "Example: `9876543210`"
    else:
        instruction = "Please enter your payment details:"
        example = "Provide necessary details"
    
    await query.edit_message_text(
        f"📝 **{payment_method} Details**\n\n{instruction}\n{example}",
        parse_mode='Markdown'
    )
    context.user_data['waiting_for_payment_details'] = True

async def ask_blockchain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    crypto_type = context.user_data['crypto_type']
    
    if crypto_type == "BTC":
        networks = [["BTC"]]
    elif crypto_type == "ETH":
        networks = [["ETH"]]
    else:  # USDT
        networks = ["TRC20", "TRON", "BNB smart chain"]
    
    keyboard = []
    for network in networks:
        if isinstance(network, list):
            keyboard.append([InlineKeyboardButton(network[0], callback_data=f"blockchain_{network[0]}")])
        else:
            keyboard.append([InlineKeyboardButton(network, callback_data=f"blockchain_{network}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔗 **Select Blockchain Network:**",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def process_swap(update: Update, context: ContextTypes.DEFAULT_TYPE, blockchain: str):
    query = update.callback_query
    user_id = query.from_user.id
    
    crypto_type = context.user_data['crypto_type']
    crypto_amount = context.user_data['amount']
    payment_method = context.user_data['payment_method']
    payment_details = context.user_data['payment_details']
    
    # Create TEMPORARY order (not confirmed yet)
    order = bot.create_temp_order(user_id, crypto_type, crypto_amount, payment_method, payment_details, blockchain)
    
    # Calculate expiry time
    expiry_time = order['expires_at'].strftime("%H:%M:%S")
    
    order_text = f"""
⏳ **Temporary Order Created - #{order['order_id']}**

📊 Order Details:
• Cryptocurrency: {crypto_type}
• Crypto Amount: {crypto_amount}
• Fee (3%): {order['crypto_fee']:.6f} {crypto_type}
• Net Crypto to Send: {order['net_crypto_amount']:.6f} {crypto_type}
• INR Value: ₹{order['inr_amount']:.2f}

💰 Fee: ₹{order['fee']:.2f} (included in crypto)
💸 You Receive: ₹{order['net_amount']:.2f}

🏦 Payment Method: {payment_method}
🔗 Blockchain: {blockchain}

⚠️ **Important Instructions:**
1. Send exactly **{order['net_crypto_amount']:.6f} {crypto_type}** to:
`{order['wallet_address']}`

2. **Network:** {blockchain}
3. **Time Limit:** 15 minutes (until {expiry_time})
4. **Order will only be placed after you submit transaction proof**

🔗 **After sending crypto, click 'Submit Transaction' to complete your order**
    """
    
    keyboard = [
        [InlineKeyboardButton("📤 Submit Transaction", callback_data=f"submit_tx_{order['order_id']}")],
        [InlineKeyboardButton("❌ Cancel Order", callback_data=f"cancel_order_{order['order_id']}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(order_text, reply_markup=reply_markup, parse_mode='Markdown')

async def ask_transaction_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text(
        "🔗 **Please send your transaction link/hash:**\n\n"
        "Example: `https://tronscan.org/#/transaction/...`\n"
        "Or transaction hash: `0x...`\n\n"
        "⚠️ **Your order will only be placed after transaction verification**",
        parse_mode='Markdown'
    )
    context.user_data['waiting_for_tx'] = True

async def cancel_temp_order(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int):
    query = update.callback_query
    
    # Delete the temporary order
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM orders WHERE order_id = ? AND status = "temp_order"', (order_id,))
    conn.commit()
    conn.close()
    
    await query.edit_message_text(
        "❌ Order cancelled. You can start a new swap with /swap",
        parse_mode='Markdown'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if context.user_data.get('waiting_for_amount'):
        try:
            amount = float(update.message.text)
            if amount <= 0:
                await update.message.reply_text("❌ Amount must be positive. Please enter a valid amount:")
                return
            
            context.user_data['amount'] = amount
            context.user_data['waiting_for_amount'] = False
            await ask_payment_method(update, context)
            
        except ValueError:
            await update.message.reply_text("❌ Invalid amount. Please enter a valid number:")
    
    elif context.user_data.get('waiting_for_payment_details'):
        payment_details = update.message.text
        context.user_data['payment_details'] = payment_details
        context.user_data['waiting_for_payment_details'] = False
        await ask_blockchain(update, context)
    
    elif context.user_data.get('waiting_for_tx'):
        tx_link = update.message.text
        order_id = context.user_data.get('current_order')
        
        if order_id:
            # Confirm the order with transaction link
            success = bot.confirm_order_with_transaction(order_id, tx_link)
            
            if success:
                await update.message.reply_text(
                    "✅ **Order Placed Successfully!** ✅\n\n"
                    "Your transaction has been submitted and order is now placed.\n"
                    "Admin will review and process your payment shortly."
                )
                
                # Notify admin about the new confirmed order
                await notify_admin_new_order(context, order_id, user_id, tx_link)
                
            else:
                await update.message.reply_text(
                    "❌ Failed to submit transaction. The order may have expired or already been processed.\n"
                    "Please start a new swap with /swap"
                )
            
            context.user_data['waiting_for_tx'] = False
            context.user_data['current_order'] = None
    
    # Admin search order
    elif context.user_data.get('admin_searching'):
        search_term = update.message.text
        context.user_data['admin_searching'] = False
        await admin_search_results(update, context, search_term)

async def notify_admin_new_order(context, order_id: int, user_id: int, tx_link: str):
    order_details = bot.get_order_details(order_id)
    user = bot.get_user(user_id)
    
    admin_text = f"""
🆕 **NEW SWAP ORDER - #{order_id}** 🆕

👤 **User Information:**
• User ID: `{user_id}`
• Name: {user[2] or 'N/A'}
• Username: @{user[1] or 'N/A'}

💰 **Order Details:**
• Order ID: #{order_id}
• Cryptocurrency: {order_details[2]}
• Crypto Amount: {order_details[3]}
• INR Value: ₹{order_details[4]:.2f}
• Blockchain: {order_details[7]}

🏦 **Payment Information:**
• Payment Method: {order_details[5]}
• Payment Details: `{order_details[6]}`

💰 **Financial Details:**
• Fee: ₹{order_details[8]:.2f}
• Net Amount to User: ₹{order_details[9]:.2f}

🔗 **Transaction Link:** {tx_link}
⏰ **Status:** ⏳ Pending Verification

🕒 **Created:** {order_details[14]}
    """
    
    keyboard = [
        [InlineKeyboardButton("✅ Approve Order", callback_data=f"admin_approve_{order_id}"),
         InlineKeyboardButton("❌ Reject Order", callback_data=f"admin_reject_{order_id}")],
        [InlineKeyboardButton("👁️ View Details", callback_data=f"admin_view_{order_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Send to admin directly
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=admin_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    # Send to admin channel (without buttons)
    admin_channel_text = f"""
🆕 **NEW ORDER #{order_id}**

👤 User: {user_id} ({user[2] or 'N/A'})
💰 {order_details[3]} {order_details[2]} → ₹{order_details[4]:.2f}
🏦 {order_details[5]}: {order_details[6]}
🔗 TX: {tx_link[:50]}...
⏰ Status: Pending Verification
    """
    await context.bot.send_message(
        chat_id=ADMIN_CHANNEL,
        text=admin_channel_text,
        parse_mode='Markdown'
    )

# Admin Panel Functions
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    # Clean up expired temporary orders
    bot.cleanup_temp_orders()
    
    keyboard = [
        [InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
         InlineKeyboardButton("📋 Pending Orders", callback_data="admin_orders")],
        [InlineKeyboardButton("💳 Withdrawal Requests", callback_data="admin_withdrawals"),
         InlineKeyboardButton("🔍 Search Order", callback_data="admin_search")],
        [InlineKeyboardButton("⚙️ Wallet Settings", callback_data="admin_wallets")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👑 **Admin Panel**",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    stats = bot.get_admin_stats()
    
    stats_text = f"""
📊 **Admin Statistics**

👥 Total Users: {stats['total_users']}
🔄 Total Orders: {stats['total_orders']}
✅ Completed Orders: {stats['completed_orders']}
💰 Total Traded: ₹{stats['total_traded']:.2f}
💸 Total Fees: ₹{stats['total_fees']:.2f}
⏳ Pending Withdrawals: {stats['pending_withdrawals']}

🔄 Crypto Prices:
• USDT: ₹{bot.get_crypto_price('USDT'):.2f}
• BTC: ₹{bot.get_crypto_price('BTC'):.2f}
• ETH: ₹{bot.get_crypto_price('ETH'):.2f}
    """
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(stats_text, reply_markup=reply_markup, parse_mode='Markdown')

async def admin_pending_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    orders = bot.get_pending_orders()
    
    if not orders:
        text = "📭 No pending orders found."
        keyboard = [[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        return
    
    text = "📋 **Pending Orders:**\n\n"
    for order in orders[:10]:
        text += f"⏳ **Order #{order[0]}**\n"
        text += f"• User: {order[16] or 'N/A'} (@{order[15] or 'N/A'})\n"
        text += f"• Crypto: {order[3]} {order[2]}\n"
        text += f"• INR: ₹{order[4]:.2f}\n"
        text += f"• Payment: {order[5]}\n"
        text += f"• Created: {order[14]}\n\n"
    
    keyboard = [
        [InlineKeyboardButton("🔄 Refresh", callback_data="admin_orders")],
        [InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def admin_view_order(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int):
    query = update.callback_query
    order_details = bot.get_order_details(order_id)
    
    if not order_details:
        await query.answer("Order not found!", show_alert=True)
        return
    
    status_icons = {
        'pending': '⏳',
        'temp_order': '📝',
        'completed': '✅',
        'rejected': '❌'
    }
    
    order_text = f"""
{status_icons.get(order_details[10], '📄')} **Order #{order_id} Details**

👤 **User Information:**
• User ID: `{order_details[1]}`
• Name: {order_details[16] or 'N/A'}
• Username: @{order_details[15] or 'N/A'}

💰 **Order Details:**
• Cryptocurrency: {order_details[2]}
• Crypto Amount: {order_details[3]}
• INR Value: ₹{order_details[4]:.2f}
• Payment Method: {order_details[5]}
• Payment Details: `{order_details[6]}`
• Blockchain: {order_details[7]}

💸 **Financial Details:**
• Fee: ₹{order_details[8]:.2f}
• Net Amount: ₹{order_details[9]:.2f}

📊 **Status:** {order_details[10]}
🔗 **Transaction:** {order_details[11] or 'Not provided'}

🕒 **Created:** {order_details[14]}
⏳ **Expires:** {order_details[13] or 'N/A'}

**User's Orders:** Use button below to view all user orders
    """
    
    keyboard = []
    if order_details[10] == 'pending':
        keyboard.append([
            InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve_{order_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject_{order_id}")
        ])
    
    keyboard.append([InlineKeyboardButton("📋 User's Orders", callback_data=f"admin_user_orders_{order_details[1]}")])
    keyboard.append([InlineKeyboardButton("🔙 Back to Orders", callback_data="admin_orders")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(order_text, reply_markup=reply_markup, parse_mode='Markdown')

async def admin_pending_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    withdrawals = bot.get_pending_withdrawals()
    
    if not withdrawals:
        text = "💳 No pending withdrawal requests."
        keyboard = [[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        return
    
    text = "💳 **Pending Withdrawal Requests:**\n\n"
    for wd in withdrawals[:10]:
        text += f"💰 **Withdrawal #{wd[0]}**\n"
        text += f"• User: {wd[5] or 'N/A'} (@{wd[4] or 'N/A'})\n"
        text += f"• Amount: ₹{wd[2]:.2f}\n"
        text += f"• Balance: ₹{wd[6]:.2f}\n"
        text += f"• Requested: {wd[4]}\n\n"
    
    # Add buttons for each withdrawal
    keyboard = []
    for wd in withdrawals[:5]:
        keyboard.append([
            InlineKeyboardButton(f"✅ Approve WD#{wd[0]}", callback_data=f"admin_approve_wd_{wd[0]}"),
            InlineKeyboardButton(f"❌ Reject WD#{wd[0]}", callback_data=f"admin_reject_wd_{wd[0]}")
        ])
    
    keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data="admin_withdrawals")])
    keyboard.append([InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def admin_search_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text(
        "🔍 **Search Order**\n\n"
        "Enter Order ID or User ID to search:\n"
        "• Order ID: `123`\n"
        "• User ID: `456789012`\n"
        "• Username: `username`",
        parse_mode='Markdown'
    )
    context.user_data['admin_searching'] = True

async def admin_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE, search_term: str):
    orders = bot.search_orders(search_term)
    
    if not orders:
        text = f"❌ No orders found for: `{search_term}`"
        keyboard = [[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        return
    
    text = f"🔍 **Search Results for `{search_term}`:**\n\n"
    for order in orders[:5]:
        status_icon = "✅" if order[10] == "completed" else "⏳" if order[10] == "pending" else "❌"
        text += f"{status_icon} **Order #{order[0]}**\n"
        text += f"• User: {order[16] or 'N/A'} (@{order[15] or 'N/A'})\n"
        text += f"• Crypto: {order[3]} {order[2]}\n"
        text += f"• INR: ₹{order[4]:.2f}\n"
        text += f"• Status: {order[10]}\n"
        text += f"• Created: {order[14]}\n\n"
    
    if len(orders) > 5:
        text += f"📄 Showing 5 of {len(orders)} orders\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def admin_wallet_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    text = "⚙️ **Wallet Settings**\n\n"
    for crypto, networks in WALLETS.items():
        text += f"**{crypto}:**\n"
        for network, address in networks.items():
            text += f"• {network}: `{address}`\n"
        text += "\n"
    
    text += "To update wallet addresses, modify the WALLETS dictionary in the code."
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def admin_approve_order(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int):
    query = update.callback_query
    
    # Update order status
    bot.update_order_status(order_id, "completed")
    
    # Get order details
    order_details = bot.get_order_details(order_id)
    
    # Notify user
    user_notification = f"""
✅ **Order Approved!** ✅

Your order #{order_id} has been approved and processed.

📊 **Details:**
• Crypto: {order_details[3]} {order_details[2]}
• INR Value: ₹{order_details[4]:.2f}
• You Received: ₹{order_details[9]:.2f}
• Payment Method: {order_details[5]}

💰 Payment has been sent to your {order_details[5]} account:
`{order_details[6]}`

Thank you for using our service! 🚀
    """
    
    try:
        await context.bot.send_message(
            chat_id=order_details[1],
            text=user_notification,
            parse_mode='Markdown'
        )
    except Exception as e:
        logging.error(f"Failed to notify user: {e}")
    
    # Update admin
    approval_text = f"""
✅ **ORDER APPROVED** ✅

📋 Order ID: #{order_id}
👤 User: {order_details[1]}
💰 Crypto: {order_details[3]} {order_details[2]}
🇮🇳 INR Value: ₹{order_details[4]:.2f}
💸 Paid: ₹{order_details[9]:.2f}
🏦 Payment Method: {order_details[5]}

🕒 Approved at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    """
    
    await query.edit_message_text(approval_text, parse_mode='Markdown')
    
    # Notify admin channel
    await context.bot.send_message(
        chat_id=ADMIN_CHANNEL,
        text=approval_text,
        parse_mode='Markdown'
    )

async def admin_reject_order(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int):
    query = update.callback_query
    
    # Update order status
    bot.update_order_status(order_id, "rejected")
    
    # Get order details
    order_details = bot.get_order_details(order_id)
    
    # Notify user
    user_notification = f"""
❌ **Order Rejected** ❌

Your order #{order_id} has been rejected.

Please contact support @ROSHAN_86 for more information.

If you've already sent the crypto, provide transaction proof to support for resolution.
    """
    
    try:
        await context.bot.send_message(
            chat_id=order_details[1],
            text=user_notification,
            parse_mode='Markdown'
        )
    except Exception as e:
        logging.error(f"Failed to notify user: {e}")
    
    # Update admin
    rejection_text = f"""
❌ **ORDER REJECTED** ❌

📋 Order ID: #{order_id}
👤 User: {order_details[1]}
💰 Crypto: {order_details[3]} {order_details[2]}
🇮🇳 INR Value: ₹{order_details[4]:.2f}

🕒 Rejected at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    """
    
    await query.edit_message_text(rejection_text, parse_mode='Markdown')
    
    # Notify admin channel
    await context.bot.send_message(
        chat_id=ADMIN_CHANNEL,
        text=rejection_text,
        parse_mode='Markdown'
    )

async def admin_approve_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE, withdrawal_id: int):
    query = update.callback_query
    
    # Get withdrawal details first
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT w.*, u.username, u.first_name 
        FROM withdrawal_requests w 
        LEFT JOIN users u ON w.user_id = u.user_id 
        WHERE w.id = ?
    ''', (withdrawal_id,))
    withdrawal = cursor.fetchone()
    conn.close()
    
    if not withdrawal:
        await query.answer("Withdrawal request not found!", show_alert=True)
        return
    
    # Update withdrawal status
    bot.update_withdrawal_status(withdrawal_id, "completed")
    
    # Notify user
    user_notification = f"""
✅ **Withdrawal Approved!** ✅

Your withdrawal request for ₹{withdrawal[2]:.2f} has been approved and processed.

The amount will be transferred to your registered payment method within 24 hours.

Thank you for using our service! 🚀
    """
    
    try:
        await context.bot.send_message(
            chat_id=withdrawal[1],
            text=user_notification,
            parse_mode='Markdown'
        )
    except Exception as e:
        logging.error(f"Failed to notify user: {e}")
    
    # Update admin
    approval_text = f"""
✅ **WITHDRAWAL APPROVED** ✅

📋 Withdrawal ID: #{withdrawal_id}
👤 User: {withdrawal[1]}
💰 Amount: ₹{withdrawal[2]:.2f}

🕒 Approved at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    """
    
    await query.edit_message_text(approval_text, parse_mode='Markdown')
    
    # Notify admin channel
    await context.bot.send_message(
        chat_id=ADMIN_CHANNEL,
        text=approval_text,
        parse_mode='Markdown'
    )

async def admin_reject_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE, withdrawal_id: int):
    query = update.callback_query
    
    # Get withdrawal details first
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT w.*, u.username, u.first_name 
        FROM withdrawal_requests w 
        LEFT JOIN users u ON w.user_id = u.user_id 
        WHERE w.id = ?
    ''', (withdrawal_id,))
    withdrawal = cursor.fetchone()
    conn.close()
    
    if not withdrawal:
        await query.answer("Withdrawal request not found!", show_alert=True)
        return
    
    # Update withdrawal status
    bot.update_withdrawal_status(withdrawal_id, "rejected")
    
    # Add back the balance to user
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE users 
        SET referral_balance = referral_balance + ? 
        WHERE user_id = ?
    ''', (withdrawal[2], withdrawal[1]))
    conn.commit()
    conn.close()
    
    # Notify user
    user_notification = f"""
❌ **Withdrawal Rejected** ❌

Your withdrawal request for ₹{withdrawal[2]:.2f} has been rejected.

The amount has been added back to your referral balance.

Please contact support @ROSHAN_86 for more information.
    """
    
    try:
        await context.bot.send_message(
            chat_id=withdrawal[1],
            text=user_notification,
            parse_mode='Markdown'
        )
    except Exception as e:
        logging.error(f"Failed to notify user: {e}")
    
    # Update admin
    rejection_text = f"""
❌ **WITHDRAWAL REJECTED** ❌

📋 Withdrawal ID: #{withdrawal_id}
👤 User: {withdrawal[1]}
💰 Amount: ₹{withdrawal[2]:.2f}

🕒 Rejected at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    """
    
    await query.edit_message_text(rejection_text, parse_mode='Markdown')
    
    # Notify admin channel
    await context.bot.send_message(
        chat_id=ADMIN_CHANNEL,
        text=rejection_text,
        parse_mode='Markdown'
    )

async def admin_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    keyboard = [
        [InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
         InlineKeyboardButton("📋 Pending Orders", callback_data="admin_orders")],
        [InlineKeyboardButton("💳 Withdrawal Requests", callback_data="admin_withdrawals"),
         InlineKeyboardButton("🔍 Search Order", callback_data="admin_search")],
        [InlineKeyboardButton("⚙️ Wallet Settings", callback_data="admin_wallets")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "👑 **Admin Panel**",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.callback_query.from_user.id
    user = bot.get_user(user_id)
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Get order stats (only completed orders)
    cursor.execute('''
        SELECT COUNT(*), COALESCE(SUM(inr_amount), 0) 
        FROM orders 
        WHERE user_id = ? AND status = 'completed'
    ''', (user_id,))
    orders_count, total_traded = cursor.fetchone()
    
    # Get referral count
    cursor.execute('SELECT COUNT(*) FROM users WHERE referred_by = ?', (user_id,))
    referral_count = cursor.fetchone()[0]
    
    conn.close()
    
    stats_text = f"""
📊 **Your Trading Stats**

🔄 Completed Swaps: {orders_count}
💰 Total Traded: ₹{total_traded:.2f}
👥 Referrals: {referral_count}
💎 Referral Balance: ₹{user[5]:.2f}
🏆 Total Earned: ₹{user[6]:.2f}
    """
    
    await update.callback_query.edit_message_text(stats_text, parse_mode='Markdown')

async def myref_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    user = bot.get_user(user_id)
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users WHERE referred_by = ?', (user_id,))
    referral_count = cursor.fetchone()[0]
    conn.close()
    
    ref_text = f"""
👥 **Your Referral Stats**

🔗 Your Referral Link:
`https://t.me/your_bot_username?start={user[3]}`

📊 Stats:
• Total Referrals: {referral_count}
• Current Balance: ₹{user[5]:.2f}
• Total Earned: ₹{user[6]:.2f}

💡 **How it works:**
Earn 20% of transaction fees from every swap made by your referrals!
    """
    
    keyboard = [
        [InlineKeyboardButton("💰 Withdraw Earnings", callback_data="withdraw_ref")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(ref_text, reply_markup=reply_markup, parse_mode='Markdown')

async def support_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    support_text = """
🆘 **Support Center**

For any issues or queries:
• Contact Support: @ROSHAN_86
• Response Time: 2-4 hours

Common Issues:
• Transaction delays? Wait 15-30 minutes
• Wrong network? Contact support immediately
• Payment issues? Provide transaction proof
    """
    await query.edit_message_text(support_text, parse_mode='Markdown')

async def withdraw_ref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    user = bot.get_user(user_id)
    
    if user[5] <= 0:
        await query.answer("❌ No balance to withdraw!", show_alert=True)
        return
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO withdrawal_requests (user_id, amount)
        VALUES (?, ?)
    ''', (user_id, user[5]))
    
    # Reset balance
    cursor.execute('UPDATE users SET referral_balance = 0 WHERE user_id = ?', (user_id,))
    
    conn.commit()
    conn.close()
    
    await query.answer("✅ Withdrawal request submitted! Admin will process it.", show_alert=True)
    
    # Notify admin
    admin_notification = f"""
🆕 **Withdrawal Request**

👤 User: {user_id} ({user[2] or 'N/A'})
💰 Amount: ₹{user[5]:.2f}
⏰ Requested: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Use /admin to manage withdrawal requests.
    """
    
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=admin_notification,
        parse_mode='Markdown'
    )
    
    # Notify admin channel
    await context.bot.send_message(
        chat_id=ADMIN_CHANNEL,
        text=admin_notification,
        parse_mode='Markdown'
    )

def main():
    """Start the bot"""
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("swap", swap))
    application.add_handler(CommandHandler("support", support))
    application.add_handler(CommandHandler("myref", myref))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("orders", view_orders))
    application.add_handler(CommandHandler("order", view_single_order))
    
    # Callback query handler
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Message handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Start the bot
    print("🤖 Crypto Swap Bot is running...")
    application.run_polling()

if __name__ == "__main__":
    main()
