import csv
from datetime import datetime

FILE_POS = "open_positions.csv"


def init_trade_log(file, columns):
    try:
        open(file, "x").write(columns)
    except FileExistsError:
        pass


def log_trade(file, symbol, side, entry, exit, score, strength, pnl, balance):

    with open(file, "a", newline="") as f:

        writer = csv.writer(f)

        writer.writerow([
            datetime.utcnow(),
            symbol,
            side,
            round(entry, 4),
            round(exit, 4),
            score,
            strength,
            round(pnl, 4),
            round(balance, 2)
        ])

def log_position(file, order):

    with open(file, "a", newline="") as f:
        writer = csv.writer(f)

        writer.writerow([
            order["id"],
            order["symbol"]
        ])