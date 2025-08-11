# bot_binance.py — versão final pronta (com suas chaves/telegram já inseridos)
# Requisitos: pip install python-binance pandas ta requests

import time, math, csv, threading, requests
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException

# ====== CONFIGURAÇÕES (edite aqui só se quiser) ======
API_KEY = "DIdAoPpHFdCK9AALRcJKegnLC9WgKP9u2snIPGQZIfrkFHcpvxzu4PvcHVEZBxHf"
API_SECRET = "Gkt7OBhkJ88L2qR119g93iywvRq8s3qBAkX9wChajQ0HBfh6hHo6IyD0v0EE0tBM"

TELEGRAM_TOKEN = "8460204181:AAGjqiq1a1w6_mBgHCVGaxvrjTw7ikaRg1U"
TELEGRAM_CHAT_ID = "6276587767"

VALOR_POR_OPERACAO = 7.0       # USDC por operação
MAX_CONCURRENT_TRADES = 2     # até 2 trades simultâneos
TP_PCT = 0.03                 # take profit 3%
SL_PCT = 0.02                 # stop loss inicial 2%

TRAILING_ENABLED = True
TRAILING_START_PCT = 0.01     # inicia trailing após +1%
TRAILING_DISTANCE_PCT = 0.015 # mantem SL 1.5% do topo

SCAN_INTERVAL = 300           # 5 minutos
CSV_LOGFILE = "historico_trades_ptbr.csv"

# Lista fixa 10 pares USDC
PARES_FIXOS = [
    "BTCUSDC","ETHUSDC","BNBUSDC","ADAUSDC","SOLUSDC",
    "XRPUSDC","LTCUSDC","DOGEUSDC","MATICUSDC","DOTUSDC"
]

# mínima concordância de sinais (ajustável)
SCORE_MIN_PARA_COMPRA = 3

# =========================================

client = Client(API_KEY, API_SECRET)
trades = {}   # trade_id -> info
lock = threading.Lock()

# ---------- util ----------
def agora_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except Exception as e:
        print(f"[{agora_str()}] Erro Telegram: {e}")

def salvar_log(row: dict):
    header = ["data_hora","par","lado","preco_entrada","preco_saida","quantidade","pnl_usdc","motivo","indicadores"]
    write_header = False
    try:
        with open(CSV_LOGFILE, 'r', encoding='utf-8'):
            pass
    except FileNotFoundError:
        write_header = True
    with open(CSV_LOGFILE, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=header)
        if write_header:
            writer.writeheader()
        writer.writerow(row)

# ---------- símbolos / filtros ----------
def get_symbol_info_safe(symbol):
    try:
        return client.get_symbol_info(symbol)
    except Exception as e:
        print(f"[{agora_str()}] Erro get_symbol_info {symbol}: {e}")
        return None

def get_filters(symbol):
    info = get_symbol_info_safe(symbol)
    if not info:
        return {}
    out = {}
    for f in info.get('filters', []):
        t = f.get('filterType')
        if t == 'LOT_SIZE':
            out['stepSize'] = float(f.get('stepSize', 0))
            out['minQty'] = float(f.get('minQty', 0))
        if t == 'MIN_NOTIONAL':
            try:
                out['minNotional'] = float(f.get('minNotional', 0))
            except:
                out['minNotional'] = float(f.get('minNotional') or 0)
        if t == 'PRICE_FILTER':
            out['tickSize'] = float(f.get('tickSize', 0))
    return out

def decimals_from_step(step):
    try:
        return max(0, int(round(-math.log10(step))))
    except:
        return 8

def format_price(symbol, price):
    f = get_filters(symbol)
    tick = f.get('tickSize')
    if tick and tick > 0:
        d = decimals_from_step(tick)
        return float(format(price, f".{d}f"))
    return float(format(price, ".8f"))

def format_qty(symbol, qty):
    f = get_filters(symbol)
    step = f.get('stepSize')
    min_q = f.get('minQty')
    if step and step > 0:
        d = decimals_from_step(step)
        try:
            qty_adj = math.floor(qty / step) * step
            qty_adj = float(format(qty_adj, f".{d}f"))
        except:
            qty_adj = float(format(qty, f".{d}f"))
    else:
        qty_adj = float(format(qty, ".8f"))
    if min_q and qty_adj < min_q:
        return None
    return qty_adj

def obter_min_notional(symbol):
    f = get_filters(symbol)
    return f.get('minNotional', 0.0)

def simbolo_permitido(symbol):
    info = get_symbol_info_safe(symbol)
    if not info:
        return False
    return info.get('status') == 'TRADING'

# ---------- dados / indicadores ----------
def fetch_klines_df(symbol, limit=200):
    try:
        kl = client.get_klines(symbol=symbol, interval=Client.KLINE_INTERVAL_5MINUTE, limit=limit)
        df = pd.DataFrame(kl, columns=[
            "open_time","open","high","low","close","volume","close_time","qav","num_trades","tbbav","tbqav","ignore"
        ])
        df['close'] = pd.to_numeric(df['close'])
        df['open'] = pd.to_numeric(df['open'])
        df['high'] = pd.to_numeric(df['high'])
        df['low'] = pd.to_numeric(df['low'])
        df['volume'] = pd.to_numeric(df['volume'])
        return df
    except Exception as e:
        print(f"[{agora_str()}] Erro fetch_klines {symbol}: {e}")
        return None

def compute_indicators(df):
    res = {}
    try:
        import ta
        df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
        rsi = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
        macd = ta.trend.MACD(df['close'])
        df['macd'] = macd.macd()
        df['macd_sig'] = macd.macd_signal()
        df['rsi'] = rsi
        df['vol_ma20'] = df['volume'].rolling(20).mean()
    except Exception:
        df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['rsi'] = 50.0
        df['macd'] = df['close'].diff()
        df['macd_sig'] = df['macd'].rolling(9).mean()
        df['vol_ma20'] = df['volume'].rolling(20).mean()

    last = df.iloc[-1]
    prev = df.iloc[-2]
    res['close'] = float(last['close'])
    res['ema20'] = float(last['ema20'])
    res['ema50'] = float(last['ema50'])
    res['ema_cross_up'] = (prev['ema20'] < prev['ema50']) and (last['ema20'] > last['ema50'])
    res['macd_cross_up'] = (prev['macd'] < prev['macd_sig']) and (last['macd'] > last['macd_sig'])
    res['rsi'] = float(last.get('rsi',50))
    res['vol_spike'] = float(last['volume']) > max(1.2 * float(last.get('vol_ma20') or 0), 1e-9)
    return res

def avaliar_entrada(symbol):
    if "PEPE" in symbol.upper(): return False, "Ignorar PEPE"
    if not simbolo_permitido(symbol): return False, "Símbolo não permitido"
    df = fetch_klines_df(symbol)
    if df is None or len(df) < 60:
        return False, "Dados insuficientes"
    ind = compute_indicators(df)
    score = 0
    motivos = []
    if ind.get('macd_cross_up'): score+=1; motivos.append("MACD↑")
    if ind.get('ema_cross_up'): score+=1; motivos.append("EMA↑")
    if 30 < ind.get('rsi',50) < 70: score+=1; motivos.append(f"RSI{int(ind.get('rsi',50))}")
    if ind.get('vol_spike'): score+=1; motivos.append("Vol↑")
    return (score >= SCORE_MIN_PARA_COMPRA), ";".join(motivos)

# ---------- ordens e gestão ----------
def place_market_buy(symbol, usdc_amount):
    try:
        ticker = client.get_symbol_ticker(symbol=symbol)
        price = float(ticker['price'])
    except Exception as e:
        print(f"[{agora_str()}] Erro ticker {symbol}: {e}")
        return None

    min_not = obter_min_notional(symbol) or 0.0
    valor_usar = max(usdc_amount, min_not)
    raw_qty = valor_usar / price if price>0 else 0
    qty = format_qty(symbol, raw_qty)
    if qty is None or qty <= 0:
        print(f"[{agora_str()}] Quantidade inválida {symbol}: raw={raw_qty} -> qty={qty}")
        return None
    try:
        ord = client.create_order(symbol=symbol, side='BUY', type='MARKET', quantity=qty)
        fills = ord.get('fills') or []
        if fills:
            total_qty = sum(float(f['qty']) for f in fills)
            total_cost = sum(float(f['qty'])*float(f['price']) for f in fills)
            entry_price = total_cost/total_qty if total_qty>0 else float(fills[0].get('price'))
        else:
            total_qty = float(ord.get('executedQty',0) or 0)
            entry_price = price
        return {"qty": float(total_qty), "entry_price": float(entry_price)}
    except BinanceAPIException as e:
        print(f"[{agora_str()}] Erro place_market_buy: {e}")
        return None
    except Exception as e:
        print(f"[{agora_str()}] Erro gen place_market_buy: {e}")
        return None

def criar_tp_sl_exchange(symbol, qty, entry_price, tp_pct=TP_PCT, sl_pct=SL_PCT):
    tp_raw = entry_price*(1+tp_pct)
    sl_trigger_raw = entry_price*(1-sl_pct)
    tp_price = format_price(symbol, tp_raw)
    sl_trigger = format_price(symbol, sl_trigger_raw)
    sl_limit = format_price(symbol, sl_trigger * (1-0.001))
    res = {"tp_order":None, "sl_order":None}
    try:
        tp = client.create_order(symbol=symbol, side='SELL', type='LIMIT', timeInForce='GTC',
                                 quantity=format_qty(symbol, qty), price=format(tp_price, '.8f'))
        res['tp_order'] = tp
    except BinanceAPIException as e:
        print(f"[{agora_str()}] Erro criando TP em {symbol}: {e}")
    except Exception as e:
        print(f"[{agora_str()}] Erro gen TP: {e}")
    try:
        sl = client.create_order(symbol=symbol, side='SELL', type='STOP_LOSS_LIMIT', timeInForce='GTC',
                                 quantity=format_qty(symbol, qty), price=format(sl_limit, '.8f'), stopPrice=format(sl_trigger, '.8f'))
        res['sl_order'] = sl
    except BinanceAPIException as e:
        print(f"[{agora_str()}] Erro criando SL em {symbol}: {e}")
    except Exception as e:
        print(f"[{agora_str()}] Erro gen SL: {e}")
    return res

def cancelar_order_by_id(symbol, orderId):
    try:
        client.cancel_order(symbol=symbol, orderId=orderId)
        return True
    except Exception as e:
        print(f"[{agora_str()}] Erro cancelando {orderId} em {symbol}: {e}")
        return False

def abrir_trade_auto(symbol):
    with lock:
        if len(trades) >= MAX_CONCURRENT_TRADES:
            return None
    ok, motivos = avaliar_entrada(symbol)
    if not ok:
        return None
    buy = place_market_buy(symbol, VALOR_POR_OPERACAO)
    if not buy:
        return None
    qty = buy['qty']; entry = buy['entry_price']
    ords = criar_tp_sl_exchange(symbol, qty, entry)
    tp_id = ords.get('tp_order',{}).get('orderId') if ords.get('tp_order') else None
    sl_id = ords.get('sl_order',{}).get('orderId') if ords.get('sl_order') else None
    trade_id = f"{symbol}-{int(time.time())}"
    trade = {
        "trade_id": trade_id,
        "symbol": symbol,
        "qty": qty,
        "entry": entry,
        "tp_id": tp_id,
        "sl_id": sl_id,
        "sl_trigger": format_price(symbol, entry*(1-SL_PCT)),
        "sl_limit": format_price(symbol, entry*(1-SL_PCT)*(1-0.001)),
        "created_at": agora_str(),
        "motivos": motivos
    }
    with lock:
        trades[trade_id] = trade
    msg = f"🟢 ABERTO {symbol} | Entrada: {entry:.8f} | Qtd: {qty} | TP: {(entry*(1+TP_PCT)):.8f} | SL: {(entry*(1-SL_PCT)):.8f} | Motivo: {motivos}"
    print(f"[{agora_str()}] {msg}")
    send_telegram(msg)
    salvar_log({"data_hora":agora_str(),"par":symbol,"lado":"COMPRA","preco_entrada":entry,"preco_saida":"","quantidade":qty,"pnl_usdc":"","motivo":"ABERTO","indicadores":motivos})
    return trade_id

def monitor_trades():
    while True:
        to_remove = []
        with lock:
            items = list(trades.items())
        for tid, t in items:
            symbol = t['symbol']
            try:
                price = float(client.get_symbol_ticker(symbol=symbol)['price'])
            except Exception as e:
                print(f"[{agora_str()}] Erro ticker em monitor {symbol}: {e}")
                continue
            if TRAILING_ENABLED and price >= t['entry']*(1+TRAILING_START_PCT):
                new_sl_trigger = format_price(symbol, price*(1-TRAILING_DISTANCE_PCT))
                new_sl_limit = format_price(symbol, new_sl_trigger*(1-0.001))
                if new_sl_trigger > t.get('sl_trigger',0):
                    old = t.get('sl_id')
                    if old:
                        try:
                            cancelar_order_by_id(symbol, old)
                        except: pass
                    try:
                        sl = client.create_order(symbol=symbol, side='SELL', type='STOP_LOSS_LIMIT', timeInForce='GTC',
                                                 quantity=format_qty(symbol,t['qty']),
                                                 price=format(new_sl_limit, '.8f'),
                                                 stopPrice=format(new_sl_trigger, '.8f'))
                        t['sl_id'] = sl.get('orderId') or sl.get('clientOrderId')
                        t['sl_trigger'] = new_sl_trigger
                        t['sl_limit'] = new_sl_limit
                        msg = f"🔁 Trailing SL atualizado {symbol} -> SL {new_sl_trigger:.8f}"
                        print(f"[{agora_str()}] {msg}")
                        send_telegram(msg)
                    except Exception as e:
                        print(f"[{agora_str()}] Erro recriar SL trailing {symbol}: {e}")
            try:
                open_orders = client.get_open_orders(symbol=symbol)
                tp_exists = any(o.get('orderId')==t.get('tp_id') or o.get('clientOrderId')==t.get('tp_id') for o in open_orders)
                sl_exists = any(o.get('orderId')==t.get('sl_id') or o.get('clientOrderId')==t.get('sl_id') for o in open_orders)
                if not tp_exists and not sl_exists:
                    exit_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
                    pnl_pct = (exit_price - t['entry'])/t['entry']
                    pnl_usdc = pnl_pct * VALOR_POR_OPERACAO
                    msg = f"🔴 FECHAMENTO {symbol} | Saída: {exit_price:.8f} | PNL: {pnl_usdc:.6f} USDC"
                    print(f"[{agora_str()}] {msg}")
                    send_telegram(msg)
                    salvar_log({"data_hora":agora_str(),"par":symbol,"lado":"FECHAMENTO","preco_entrada":t['entry'],"preco_saida":exit_price,"quantidade":t['qty'],"pnl_usdc":round(pnl_usdc,6),"motivo":"TP/SL executado","indicadores":t.get('motivos','')})
                    with lock:
                        to_remove.append(tid)
            except Exception as e:
                print(f"[{agora_str()}] Erro checando ordens abertas {symbol}: {e}")
        if to_remove:
            with lock:
                for tid in to_remove:
                    trades.pop(tid, None)
        time.sleep(5)

def main():
    ok, err = True, None
    try:
        client.get_account()
    except Exception as e:
        ok = False; err = str(e)
    if not ok:
        print(f"[{agora_str()}] Erro credenciais/perm: {err}")
        send_telegram(f"⚠️ Bot parado: erro credenciais/perm: {err}")
        return
    send_telegram(f"🤖 Bot iniciado — valor por operação: {VALOR_POR_OPERACAO} USDC. Intervalo: {SCAN_INTERVAL}s")
    print(f"[{agora_str()}] Iniciando bot. Valor por trade: {VALOR_POR_OPERACAO} USDC. Intervalo: {SCAN_INTERVAL}s")
    t = threading.Thread(target=monitor_trades, daemon=True)
    t.start()
    symbols = PARES_FIXOS.copy()
    while True:
        for s in symbols:
            with lock:
                if len(trades) >= MAX_CONCURRENT_TRADES:
                    break
            try:
                tid = abrir_trade_auto(s)
                if tid:
                    time.sleep(1)
            except Exception as e:
                print(f"[{agora_str()}] Erro ao tentar abrir {s}: {e}")
        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()
