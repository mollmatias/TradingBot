"""
PaperExecutor
─────────────
Simula exactamente la interfaz de BitgetExecutor sin enviar ninguna orden
a Bitget. Usa precios reales de la API (solo lectura) para simular entradas,
salidas por SL/TP, PnL y fees.

Uso: cambiar DRY_RUN = True en paper_trader.py. Para volver a real, DRY_RUN = False.
No hay que tocar nada más.
"""

import ccxt
import math
import time
from datetime import datetime
from telegram import send_telegram
from utils.trade_logger import log_position


class PaperExecutor:

    def __init__(self, api_key, api_secret, passphrase,
                 initial_balance=1000.0, taker_fee=0.0006):

        # Conexión real a Bitget — SOLO para leer precios, nunca para órdenes
        self.exchange = ccxt.bitget({
            "apiKey":   api_key,
            "secret":   api_secret,
            "password": passphrase,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
                "createMarketBuyOrderRequiresPrice": False
            }
        })

        self.taker_fee       = taker_fee
        self.positions       = {}           # posiciones simuladas abiertas
        self.paper_balance   = initial_balance
        self.initial_balance = initial_balance
        self.closed_trades   = []           # historial completo de trades cerrados

        # Estadísticas acumuladas
        self.stats = {
            "total_trades":  0,
            "winners":       0,
            "losers":        0,
            "total_pnl":     0.0,
            "total_fees":    0.0,
            "peak_balance":  initial_balance,
            "max_drawdown":  0.0,
        }

        print(f"📋 PaperExecutor iniciado | Balance virtual: ${initial_balance:.2f}")

    # ──────────────────────────────────────────────
    # Balance
    # ──────────────────────────────────────────────

    def get_balance(self):
        return self.paper_balance

    def get_total_balance(self):
        """
        Balance virtual = cash disponible + PnL no realizado de posiciones abiertas.
        Esto hace que dynamic_risk calcule el size sobre un balance realista,
        igual que en real donde el balance incluye el margen de posiciones abiertas.
        """
        unrealized = 0.0

        for symbol, pos in self.positions.items():
            try:
                ticker = self.exchange.fetch_ticker(symbol)
                price  = ticker["last"]
                if pos["side"] == "LONG":
                    unrealized += (price - pos["entry"]) * pos["size"]
                else:
                    unrealized += (pos["entry"] - price) * pos["size"]
            except Exception:
                pass

        return self.paper_balance + unrealized

    # ──────────────────────────────────────────────
    # Chequeos de posición
    # ──────────────────────────────────────────────

    def has_position(self, symbol):
        return symbol in self.positions

    def load_open_positions(self):
        """
        En paper trading no hay posiciones previas en Bitget.
        Devuelve lista vacía para que el arranque de paper_trader.py
        no intente recuperar posiciones reales.
        """
        return []

    # ──────────────────────────────────────────────
    # Abrir posición simulada
    # ──────────────────────────────────────────────

    def open_position(self, symbol, side, size, tp, sl, atr, score, strength):

        try:
            ticker      = self.exchange.fetch_ticker(symbol)
            entry_price = ticker["last"]
        except Exception as e:
            print(f"⚠️ PaperExecutor: no se pudo obtener precio de {symbol}: {e}")
            return

        size = self._round_size(size, symbol)

        notional = size * entry_price
        if notional < 5:
            print(f"📋 PAPER | {symbol}: notional (${notional:.2f}) menor a $5. Orden descartada.")
            return

        # Calcular margen requerido (apalancamiento x20 por defecto)
        required_margin = notional / 20
        if required_margin > self.paper_balance:
            print(f"📋 PAPER | {symbol}: margen requerido (${required_margin:.2f}) > balance (${self.paper_balance:.2f}). Orden descartada.")
            send_telegram(f"📋 PAPER ⚠️ Balance insuficiente para {side} en {symbol}")
            return

        entry_fee = entry_price * size * self.taker_fee
        self.paper_balance -= entry_fee  # descontamos fee de entrada

        risk_usdt = abs(entry_price - sl) * size
        risk_pct  = (risk_usdt / self.get_total_balance()) * 100

        self.positions[symbol] = {
            "symbol":        symbol,
            "side":          side,
            "entry":         entry_price,
            "size":          size,
            "original_size": size,
            "tp":            tp,
            "sl":            sl,
            "initial_sl":    sl,
            "trail_on":      False,
            "partial_closed": False,
            "be_set":        False,
            "atr":           atr,
            "score":         score,
            "strength":      strength,
            "opened_at":     time.time(),
        }

        print(
            f"📋 PAPER 🟢 OPEN {side} {symbol} | "
            f"entry={entry_price:.4f} | size={size} | "
            f"SL={sl:.4f} | TP={tp:.4f} | "
            f"riesgo=${risk_usdt:.2f} ({risk_pct:.2f}%)"
        )

        send_telegram(
            f"📋 <b>PAPER TRADE</b>\n"
            f"🟢 <b>OPEN {strength} {side} | score={score}</b>\n"
            f"📌 {symbol}\n"
            f"💰 Entry: {entry_price:.4f}\n"
            f"🎯 TP: {tp:.4f}\n"
            f"🛑 SL: {sl:.4f}\n"
            f"📊 Riesgo: ${risk_usdt:.2f} ({risk_pct:.2f}%)\n"
            f"💼 Balance virtual: ${self.paper_balance:.2f}"
        )

    # ──────────────────────────────────────────────
    # Chequear posiciones cerradas (por SL o TP alcanzado)
    # ──────────────────────────────────────────────

    def check_closed_positions(self):
        """
        Simula el cierre de posiciones comparando high/low del ticker contra SL y TP.

        Fixes aplicados:
        1. Tiempo mínimo de vida: no evalúa SL/TP en los primeros 30 segundos
           después de abrir — evita cierres instantáneos por ruido de spread.
        2. Usa bid/ask para simular SL de forma más realista:
           - SL de SHORT: se toca cuando el ask sube al nivel del SL
           - SL de LONG:  se toca cuando el bid baja al nivel del SL
           Si bid/ask no están disponibles, usa last como fallback.
        """
        closed = []
        now    = time.time()

        for symbol, pos in list(self.positions.items()):

            # Fix 1: ignorar posición recién abierta (< 30 segundos)
            age = now - pos.get("opened_at", now)
            if age < 30:
                continue

            try:
                ticker = self.exchange.fetch_ticker(symbol)
            except Exception:
                continue

            last = ticker.get("last") or ticker.get("close")
            bid  = ticker.get("bid")  or last
            ask  = ticker.get("ask")  or last

            side        = pos["side"]
            sl          = pos["sl"]
            tp          = pos["tp"]
            exit_price  = None
            exit_reason = None

            if side == "LONG":
                # Para un LONG: el SL se toca cuando el precio cae (usamos bid)
                # el TP se toca cuando el precio sube (usamos ask)
                if bid <= sl:
                    exit_price  = sl
                    exit_reason = "SL"
                elif ask >= tp:
                    exit_price  = tp
                    exit_reason = "TP"
            else:  # SHORT
                # Para un SHORT: el SL se toca cuando el precio sube (usamos ask)
                # el TP se toca cuando el precio cae (usamos bid)
                if ask >= sl:
                    exit_price  = sl
                    exit_reason = "SL"
                elif bid <= tp:
                    exit_price  = tp
                    exit_reason = "TP"

            if exit_price is not None:
                closed.append(
                    self._close_position(symbol, pos, exit_price, exit_reason)
                )

        return closed

    def _close_position(self, symbol, pos, exit_price, reason="manual"):

        side = pos["side"]
        size = pos["size"]

        if side == "LONG":
            gross_pnl = (exit_price - pos["entry"]) * size
        else:
            gross_pnl = (pos["entry"] - exit_price) * size

        entry_fee = pos["entry"] * size * self.taker_fee
        exit_fee  = exit_price  * size * self.taker_fee
        fees      = entry_fee + exit_fee  # entry fee ya se descontó al abrir, pero lo registramos
        net_pnl   = gross_pnl - exit_fee  # solo descontamos exit fee (entry ya fue descontado)

        self.paper_balance += net_pnl

        # Actualizar estadísticas
        self.stats["total_trades"] += 1
        self.stats["total_pnl"]    += net_pnl
        self.stats["total_fees"]   += fees

        if net_pnl >= 0:
            self.stats["winners"] += 1
        else:
            self.stats["losers"] += 1

        if self.paper_balance > self.stats["peak_balance"]:
            self.stats["peak_balance"] = self.paper_balance
        else:
            drawdown = (self.stats["peak_balance"] - self.paper_balance) / self.stats["peak_balance"] * 100
            if drawdown > self.stats["max_drawdown"]:
                self.stats["max_drawdown"] = drawdown

        win_rate = (
            self.stats["winners"] / self.stats["total_trades"] * 100
            if self.stats["total_trades"] > 0 else 0
        )

        pos["exit"]      = exit_price
        pos["pnl"]       = net_pnl
        pos["fees"]      = fees
        pos["closed_at"] = time.time()
        pos["reason"]    = reason

        del self.positions[symbol]

        emoji = "✅" if net_pnl >= 0 else "❌"

        print(
            f"📋 PAPER {emoji} CLOSE {side} {symbol} | "
            f"entry={pos['entry']:.4f} exit={exit_price:.4f} | "
            f"PnL=${net_pnl:.2f} ({reason}) | "
            f"Balance=${self.paper_balance:.2f}"
        )

        send_telegram(
            f"📋 <b>PAPER TRADE</b>\n"
            f"{emoji} <b>CLOSE {side} {symbol}</b> — {reason}\n"
            f"💰 Entry: {pos['entry']:.4f} → Exit: {exit_price:.4f}\n"
            f"📊 PnL: ${net_pnl:.2f} | Fees: ${exit_fee:.4f}\n"
            f"💼 Balance virtual: ${self.paper_balance:.2f}\n"
            f"📈 Win rate: {win_rate:.1f}% "
            f"({self.stats['winners']}W / {self.stats['losers']}L)\n"
            f"📉 Max drawdown: {self.stats['max_drawdown']:.2f}%"
        )

        return pos

    # ──────────────────────────────────────────────
    # Update SL (solo actualiza el dict interno, no llama a Bitget)
    # ──────────────────────────────────────────────

    def update_sl(self, symbol, side, size, new_sl):
        if symbol in self.positions:
            old_sl = self.positions[symbol]["sl"]
            self.positions[symbol]["sl"] = new_sl
            print(f"📋 PAPER | SL actualizado {symbol}: {old_sl:.4f} → {new_sl:.4f}")

    # ──────────────────────────────────────────────
    # Cierre parcial simulado
    # ──────────────────────────────────────────────

    def close_partial(self, symbol, side, size):
        if symbol not in self.positions:
            return

        pos = self.positions[symbol]

        try:
            ticker     = self.exchange.fetch_ticker(symbol)
            exit_price = ticker["last"]
        except Exception:
            return

        if pos["side"] == "LONG":
            partial_pnl = (exit_price - pos["entry"]) * size
        else:
            partial_pnl = (pos["entry"] - exit_price) * size

        exit_fee = exit_price * size * self.taker_fee
        net_pnl  = partial_pnl - exit_fee

        self.paper_balance         += net_pnl
        self.positions[symbol]["size"] -= size

        print(
            f"📋 PAPER 🟡 PARTIAL CLOSE {symbol} | "
            f"size={size} | PnL=${net_pnl:.2f} | "
            f"Restante={self.positions[symbol]['size']}"
        )

    # ──────────────────────────────────────────────
    # Mark price (usa precio real de Bitget)
    # ──────────────────────────────────────────────

    def get_mark_price(self, symbol):
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            if "markPrice" in ticker.get("info", {}):
                return float(ticker["info"]["markPrice"])
            if "mark" in ticker:
                return float(ticker["mark"])
            return float(ticker["last"])
        except Exception:
            return self.positions.get(symbol, {}).get("entry", 0)

    # ──────────────────────────────────────────────
    # Helpers internos
    # ──────────────────────────────────────────────

    def _round_size(self, size, symbol):
        try:
            market     = self.exchange.market(symbol)
            precision  = market.get("precision", {}).get("amount")
            min_amount = market.get("limits", {}).get("amount", {}).get("min")

            if precision is not None:
                if isinstance(precision, int):
                    factor = 10 ** precision
                    size   = math.floor(size * factor) / factor
                else:
                    size = math.floor(size / precision) * precision
                    size = round(size, 10)

            if min_amount is not None and size < min_amount:
                print(f"📐 PAPER | {symbol}: size ({size:.6f}) < mínimo ({min_amount}). Usando mínimo.")
                size = min_amount

        except Exception:
            pass

        return size

    def print_summary(self):
        """Imprime un resumen del estado actual del paper trading."""
        t = self.stats["total_trades"]
        w = self.stats["winners"]
        l = self.stats["losers"]
        wr = (w / t * 100) if t > 0 else 0
        ret = ((self.paper_balance - self.initial_balance) / self.initial_balance) * 100

        print("\n" + "─" * 50)
        print(f"📋 PAPER TRADING SUMMARY")
        print(f"  Balance inicial : ${self.initial_balance:.2f}")
        print(f"  Balance actual  : ${self.paper_balance:.2f}  ({ret:+.2f}%)")
        print(f"  PnL total       : ${self.stats['total_pnl']:.2f}")
        print(f"  Fees totales    : ${self.stats['total_fees']:.4f}")
        print(f"  Trades          : {t}  ({w}W / {l}L)  WR={wr:.1f}%")
        print(f"  Max drawdown    : {self.stats['max_drawdown']:.2f}%")
        print(f"  Pos. abiertas   : {len(self.positions)}")
        print("─" * 50 + "\n")