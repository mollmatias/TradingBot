import requests
import time
import csv

from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

BOT_ACTIVE = True

last_update_id = None


def get_updates():

    global last_update_id

    url = f"{BASE_URL}/getUpdates"

    params = {}

    if last_update_id:
        params["offset"] = last_update_id + 1

    r = requests.get(url, params=params).json()

    return r["result"]


def send_message(text):

    url = f"{BASE_URL}/sendMessage"

    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    })

def show_positions(executor):

    if not executor.positions:
        send_message("No hay posiciones abiertas")
        return

    msg = "📊 Posiciones abiertas\n\n"

    for symbol, pos in executor.positions.items():

        mark = executor.get_mark_price(symbol)

        if pos["side"] == "LONG":
            pnl = (mark - pos["entry"]) * pos["size"]
        else:
            pnl = (pos["entry"] - mark) * pos["size"]

        margin = pos["entry"] * pos["size"] / 20

        msg += (
            f"{symbol}\n"
            f"Lado: {pos['side']}\n"
            f"Entry: {round(pos['entry'],4)}\n"
            f"Mark: {round(mark,4)}\n"
            f"Margin: {round(margin,2)} USDT\n"
            f"PnL: {round(pnl,3)} USDT\n\n"
        )

    send_message(msg)

def show_pairs(symbols):

    msg = "📊 Pares activos\n\n"

    for s in symbols:
        msg += f"{s}\n"

    send_message(msg)

def add_pair(symbol, symbols):

    pair = f"{symbol}/USDT:USDT"

    if pair not in symbols:

        symbols.append(pair)

        send_message(f"Par agregado {pair}")

    else:

        send_message("Ese par ya está activo")

def remove_pair(symbol, symbols):

    pair = f"{symbol}/USDT:USDT"

    if pair in symbols:

        symbols.remove(pair)

        send_message(f"Par eliminado {pair}")

    else:

        send_message("Ese par no existe")

def show_last_trades(file):

    rows = []

    with open(file) as f:
        reader = csv.reader(f)

        for r in reader:
            rows.append(r)

    last = rows[-15:]

    msg = "📈 Últimos trades\n\n"

    for r in last:

        msg += (
            f"{r[1]} {r[2]}\n"
            f"PnL: {r[5]}\n\n"
        )

    send_message(msg)

def show_balance(executor):

    balance = executor.get_total_balance()

    send_message(
        f"💰 Balance total\n\n{round(balance,2)} USDT"
    )

from datetime import datetime

def pnl_today(file):

    today = datetime.utcnow().date()

    pnl = 0

    with open(file) as f:

        reader = csv.reader(f)

        next(reader)

        for r in reader:

            trade_time = datetime.fromisoformat(r[0]).date()

            if trade_time == today:

                pnl += float(r[5])

    send_message(
        f"📈 PnL hoy\n\n{round(pnl,2)} USDT"
    )

from datetime import timedelta

def pnl_week(file):

    now = datetime.utcnow()

    pnl = 0

    with open(file) as f:

        reader = csv.reader(f)

        next(reader)

        for r in reader:

            trade_time = datetime.fromisoformat(r[0])

            if now - trade_time < timedelta(days=7):

                pnl += float(r[5])

    send_message(
        f"📊 PnL 7 días\n\n{round(pnl,2)} USDT"
    )

def close_all_positions(executor):

    if not executor.positions:

        send_message("No hay posiciones abiertas")
        return

    for symbol, pos in executor.positions.items():

        executor.close_partial(
            symbol,
            pos["side"],
            pos["size"]
        )

    send_message("⚠️ Todas las posiciones cerradas")

def pause_bot():

    global BOT_ACTIVE

    BOT_ACTIVE = False

    send_message("⏸️ Bot pausado")

def resume_bot():

    global BOT_ACTIVE

    BOT_ACTIVE = True

    send_message("▶️ Bot reanudado")
    

def show_status(executor, trades_file):

    balance = executor.get_total_balance()

    open_trades = len(executor.positions)

    pnl_today = 0

    from datetime import datetime

    today = datetime.utcnow().date()

    with open(trades_file) as f:

        reader = csv.reader(f)
        next(reader)

        for r in reader:

            trade_time = datetime.fromisoformat(r[0]).date()

            if trade_time == today:

                pnl_today += float(r[5])

    msg = (
        f"🤖 BOT STATUS\n\n"
        f"Balance: {round(balance,2)} USDT\n"
        f"PnL hoy: {round(pnl_today,2)} USDT\n"
        f"Posiciones abiertas: {open_trades}\n"
    )

    send_message(msg)

def equity_curve(file):

    balances = []

    with open(file) as f:

        reader = csv.reader(f)
        next(reader)

        for r in reader:

            balances.append(float(r[6]))

    if not balances:

        send_message("No hay datos de equity")
        return

    last = balances[-10:]

    msg = "📈 Equity reciente\n\n"

    for b in last:

        msg += f"{round(b,2)}\n"

    send_message(msg)
    
def process_commands(symbols, executor, trades_file):

    updates = get_updates()

    for u in updates:

        global last_update_id
        last_update_id = u["update_id"]

        if "message" not in u:
            continue

        text = u["message"]["text"]

        if text == "/positions":

            show_positions(executor)

        elif text == "/pairs":

            show_pairs(symbols)

        elif text.startswith("/addpair"):

            symbol = text.split(" ")[1]
            add_pair(symbol, symbols)

        elif text.startswith("/removepair"):

            symbol = text.split(" ")[1]
            remove_pair(symbol, symbols)

        elif text == "/trades":

            show_last_trades(trades_file)

        elif text == "/balance":

            show_balance(executor)

        elif text == "/pnl_today":

            pnl_today(trades_file)

        elif text == "/pnl_week":

            pnl_week(trades_file)

        elif text == "/closeall":

            close_all_positions(executor)

        elif text == "/pause":

            pause_bot()

        elif text == "/resume":

            resume_bot()
        elif text == "/status":

            show_status(executor, trades_file)

        elif text == "/equity":

            equity_curve(trades_file)
