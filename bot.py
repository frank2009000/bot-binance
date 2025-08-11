from binance.client import Client
import requests
import time

# === CONFIGURAÇÕES ===
API_KEY = "JgW75m0nWxeVIbndl7se0xtIpHCl3IPkcUpLYWi8y4Cj6tkXP0hvalvs4qgceCjk"
API_SECRET = "iqN7AsceQdhoU0zueeLuXxvXDE49QnsyEOK5sbAxn78jHBWDgBIiVtcVcPYmFUz9"
TOKEN = "SEU_TOKEN_DO_TELEGRAM"
CHAT_ID = "SEU_CHAT_ID_DO_TELEGRAM"

# Limites do alerta
PRECO_MIN = 9
PRECO_MAX = 15

client = Client(API_KEY, API_SECRET)

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    requests.post(url, data=payload)

ultimo_alerta = None

while True:
    try:
        ticker = client.get_symbol_ticker(symbol="PEPEUSDC")
        preco = float(ticker['price']) * 1_000_000  # PEPE tem muitas casas decimais

        if preco <= PRECO_MIN and ultimo_alerta != "min":
            send_telegram(f"⚠️ PEPE abaixo do mínimo: {preco:.2f}")
            ultimo_alerta = "min"

        elif preco >= PRECO_MAX and ultimo_alerta != "max":
            send_telegram(f"🚀 PEPE acima do máximo: {preco:.2f}")
            ultimo_alerta = "max"

        time.sleep(30)

    except Exception as e:
        send_telegram(f"Erro: {e}")
        time.sleep(10)
