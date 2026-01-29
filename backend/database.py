# Database operations
import aiosqlite
from config import DB_PATH, config
import logging

logger = logging.getLogger(__name__)

async def init_db():
    """Initialize SQLite database"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT UNIQUE,
                entry_time TEXT,
                exit_time TEXT,
                option_type TEXT,
                strike INTEGER,
                expiry TEXT,
                entry_price REAL,
                exit_price REAL,
                qty INTEGER,
                pnl REAL,
                exit_reason TEXT,
                mode TEXT,
                index_name TEXT,
                created_at TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS daily_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE,
                total_trades INTEGER,
                total_pnl REAL,
                max_drawdown REAL,
                daily_stop_triggered INTEGER,
                mode TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS candle_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                candle_number INTEGER,
                index_name TEXT,
                high REAL,
                low REAL,
                close REAL,
                supertrend_value REAL,
                macd_value REAL,
                signal_status TEXT,
                created_at TEXT
            )
        ''')
        await db.commit()
        
        # Migration: Add index_name column if it doesn't exist
        try:
            cursor = await db.execute("PRAGMA table_info(trades)")
            columns = [row[1] for row in await cursor.fetchall()]
            if 'index_name' not in columns:
                await db.execute("ALTER TABLE trades ADD COLUMN index_name TEXT DEFAULT 'NIFTY'")
                await db.commit()
                logger.info("[DB] Added index_name column to trades table")
        except Exception as e:
            logger.error(f"[DB] Migration error: {e}")

async def load_config():
    """Load config from database"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT key, value FROM config') as cursor:
                rows = await cursor.fetchall()
                logger.info(f"[DB] Loaded {len(rows)} config entries from database")
                for key, value in rows:
                    if key in config:
                        # Integer fields
                        if key in ['order_qty', 'max_trades_per_day', 'candle_interval', 'supertrend_period', 'min_trade_gap']:
                            config[key] = int(value)
                        # Float fields
                        elif key in ['daily_max_loss', 'initial_stoploss', 'max_loss_per_trade', 'trail_start_profit', 'trail_step', 'target_points', 'risk_per_trade', 'supertrend_multiplier']:
                            config[key] = float(value)
                        # Boolean fields
                        elif key in ['trade_only_on_flip']:
                            config[key] = value.lower() in ('true', '1', 'yes')
                        else:
                            config[key] = value
    except Exception as e:
        logger.error(f"Error loading config: {e}")

async def save_config():
    """Save config to database"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            for key, value in config.items():
                await db.execute(
                    'INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)',
                    (key, str(value))
                )
            await db.commit()
    except Exception as e:
        logger.error(f"Error saving config: {e}")

async def save_trade(trade_data: dict):
    """Save trade to database"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('''
                INSERT INTO trades (trade_id, entry_time, option_type, strike, expiry, entry_price, qty, mode, index_name, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                trade_data['trade_id'],
                trade_data['entry_time'],
                trade_data['option_type'],
                trade_data['strike'],
                trade_data['expiry'],
                trade_data['entry_price'],
                trade_data['qty'],
                trade_data['mode'],
                trade_data.get('index_name', 'NIFTY'),
                trade_data['created_at']
            ))
            await db.commit()
            logger.info(f"[DB] Trade saved: {trade_data['trade_id']}")
    except Exception as e:
        logger.error(f"[DB] Error saving trade: {e}")

async def update_trade_exit(trade_id: str, exit_time: str, exit_price: float, pnl: float, exit_reason: str):
    """Update trade with exit details"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('''
                UPDATE trades 
                SET exit_time = ?, exit_price = ?, pnl = ?, exit_reason = ?
                WHERE trade_id = ?
            ''', (exit_time, exit_price, pnl, exit_reason, trade_id))
            await db.commit()
            logger.info(f"[DB] Trade exit updated: {trade_id}, PnL: {pnl:.2f}")
    except Exception as e:
        logger.error(f"[DB] Error updating trade exit: {e}")

async def get_trades(limit: int = None) -> list:
    """Get trade history
    
    Args:
        limit: Number of trades to fetch. None = all trades
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if limit:
            async with db.execute(
                'SELECT * FROM trades ORDER BY created_at DESC LIMIT ?',
                (limit,)
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            # Fetch all trades
            async with db.execute(
                'SELECT * FROM trades ORDER BY created_at DESC'
            ) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]

async def get_trade_analytics() -> dict:
    """Get comprehensive trade analytics with advanced metrics"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        # Fetch all completed trades
        async with db.execute(
            'SELECT * FROM trades WHERE pnl IS NOT NULL ORDER BY created_at DESC'
        ) as cursor:
            trades = await cursor.fetchall()
        
        trades = [dict(row) for row in trades]
        
        if not trades:
            return {
                'total_trades': 0,
                'total_pnl': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0,
                'avg_win': 0,
                'avg_loss': 0,
                'profit_factor': 0,
                'max_profit': 0,
                'max_loss': 0,
                'avg_trade_pnl': 0,
                'trades_by_type': {},
                'trades_by_index': {},
                'trades_by_exit_reason': {},
                'drawdown_analysis': {},
                'trades': []
            }
        
        total_trades = len(trades)
        total_pnl = sum(t['pnl'] for t in trades)
        winning_trades = [t for t in trades if t['pnl'] > 0]
        losing_trades = [t for t in trades if t['pnl'] < 0]
        
        win_count = len(winning_trades)
        loss_count = len(losing_trades)
        total_profit = sum(t['pnl'] for t in winning_trades)
        total_loss = abs(sum(t['pnl'] for t in losing_trades))
        
        # Calculate statistics
        win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0
        avg_win = total_profit / win_count if win_count > 0 else 0
        avg_loss = total_loss / loss_count if loss_count > 0 else 0
        profit_factor = total_profit / total_loss if total_loss > 0 else (total_profit if total_profit > 0 else 0)
        avg_trade_pnl = total_pnl / total_trades if total_trades > 0 else 0
        
        # Calculate standard deviation and Sharpe ratio
        if len(trades) > 1:
            pnls = [t['pnl'] for t in trades]
            mean_pnl = sum(pnls) / len(pnls)
            variance = sum((x - mean_pnl) ** 2 for x in pnls) / len(pnls)
            std_dev = variance ** 0.5
            sharpe_ratio = (avg_trade_pnl / std_dev) if std_dev > 0 else 0
        else:
            std_dev = 0
            sharpe_ratio = 0
        
        # Calculate consecutive wins/losses
        max_consecutive_wins = 0
        max_consecutive_losses = 0
        current_wins = 0
        current_losses = 0
        
        for trade in trades:
            if trade['pnl'] > 0:
                current_wins += 1
                max_consecutive_wins = max(max_consecutive_wins, current_wins)
                current_losses = 0
            else:
                current_losses += 1
                max_consecutive_losses = max(max_consecutive_losses, current_losses)
                current_wins = 0
        
        # Calculate running maximum and drawdown
        running_pnl = 0
        running_max = 0
        max_drawdown = 0
        drawdown_values = []
        
        for trade in reversed(trades):  # Process in chronological order
            running_pnl += trade['pnl']
            if running_pnl > running_max:
                running_max = running_pnl
            current_drawdown = running_max - running_pnl
            max_drawdown = max(max_drawdown, current_drawdown)
            drawdown_values.append(current_drawdown)
        
        # Trades by option type
        trades_by_type = {}
        for trade in trades:
            opt_type = trade['option_type']
            if opt_type not in trades_by_type:
                trades_by_type[opt_type] = []
            trades_by_type[opt_type].append(trade)
        
        # Trades by index
        trades_by_index = {}
        for trade in trades:
            index = trade.get('index_name', 'UNKNOWN')
            if index not in trades_by_index:
                trades_by_index[index] = []
            trades_by_index[index].append(trade)
        
        # Trades by exit reason
        trades_by_exit_reason = {}
        for trade in trades:
            reason = trade.get('exit_reason', 'Unknown')
            if reason not in trades_by_exit_reason:
                trades_by_exit_reason[reason] = []
            trades_by_exit_reason[reason].append(trade)
        
        # Group by day for daily stats
        daily_stats = {}
        for trade in trades:
            entry_date = trade['entry_time'].split('T')[0] if trade['entry_time'] else 'Unknown'
            if entry_date not in daily_stats:
                daily_stats[entry_date] = {'trades': 0, 'pnl': 0, 'wins': 0}
            daily_stats[entry_date]['trades'] += 1
            daily_stats[entry_date]['pnl'] += trade['pnl']
            if trade['pnl'] > 0:
                daily_stats[entry_date]['wins'] += 1
        
        return {
            'total_trades': total_trades,
            'total_pnl': round(total_pnl, 2),
            'winning_trades': win_count,
            'losing_trades': loss_count,
            'win_rate': round(win_rate, 2),
            'avg_win': round(avg_win, 2),
            'avg_loss': round(avg_loss, 2),
            'profit_factor': round(profit_factor, 2),
            'max_profit': round(max((t['pnl'] for t in trades), default=0), 2),
            'max_loss': round(min((t['pnl'] for t in trades), default=0), 2),
            'avg_trade_pnl': round(avg_trade_pnl, 2),
            'std_dev': round(std_dev, 2),
            'sharpe_ratio': round(sharpe_ratio, 2),
            'max_consecutive_wins': max_consecutive_wins,
            'max_consecutive_losses': max_consecutive_losses,
            'max_drawdown': round(max_drawdown, 2),
            'avg_drawdown': round(sum(drawdown_values) / len(drawdown_values), 2) if drawdown_values else 0,
            'trading_days': len(daily_stats),
            'avg_trades_per_day': round(total_trades / len(daily_stats), 2) if daily_stats else 0,
            'trades_by_type': {
                opt_type: {
                    'count': len(type_trades),
                    'pnl': round(sum(t['pnl'] for t in type_trades), 2),
                    'win_rate': round(len([t for t in type_trades if t['pnl'] > 0]) / len(type_trades) * 100, 2) if type_trades else 0
                }
                for opt_type, type_trades in trades_by_type.items()
            },
            'trades_by_index': {
                index: {
                    'count': len(index_trades),
                    'pnl': round(sum(t['pnl'] for t in index_trades), 2),
                    'win_rate': round(len([t for t in index_trades if t['pnl'] > 0]) / len(index_trades) * 100, 2) if index_trades else 0
                }
                for index, index_trades in trades_by_index.items()
            },
            'trades_by_exit_reason': {
                reason: {
                    'count': len(reason_trades),
                    'pnl': round(sum(t['pnl'] for t in reason_trades), 2),
                    'win_rate': round(len([t for t in reason_trades if t['pnl'] > 0]) / len(reason_trades) * 100, 2) if reason_trades else 0
                }
                for reason, reason_trades in trades_by_exit_reason.items()
            },
            'daily_stats': daily_stats,
            'trades': trades
        }
async def save_candle_data(candle_number: int, index_name: str, high: float, low: float, close: float, 
                          supertrend_value: float, macd_value: float, signal_status: str):
    """Save candle data for analysis"""
    try:
        from datetime import datetime, timezone
        timestamp = datetime.now(timezone.utc).isoformat()
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                '''INSERT INTO candle_data 
                   (timestamp, candle_number, index_name, high, low, close, supertrend_value, macd_value, signal_status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (timestamp, candle_number, index_name, high, low, close, supertrend_value, macd_value, signal_status, timestamp)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"[DB] Error saving candle data: {e}")

async def get_candle_data(limit: int = 1000, index_name: str = None):
    """Retrieve candle data for analysis"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            
            if index_name:
                query = f'SELECT * FROM candle_data WHERE index_name = ? ORDER BY candle_number DESC LIMIT {limit}'
                async with db.execute(query, (index_name,)) as cursor:
                    rows = await cursor.fetchall()
            else:
                query = f'SELECT * FROM candle_data ORDER BY candle_number DESC LIMIT {limit}'
                async with db.execute(query) as cursor:
                    rows = await cursor.fetchall()
            
            return [dict(row) for row in reversed(rows)]  # Return in ascending order
    except Exception as e:
        logger.error(f"[DB] Error retrieving candle data: {e}")
        return []