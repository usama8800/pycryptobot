import json
import logging
import math
import time
from datetime import datetime

import mezmorize
import numpy as np
import pandas as pd
from binance.client import BinanceAPIException, Client
from pandas.core.frame import DataFrame

from config import Config

pd.set_option(
    "display.float_format",
    lambda x: x
    if math.isnan(x)
    else ("%.0f" if int(x) == x else "%0.0f" if abs(x) < 0.0001 else "%.4f")
    % (-x if -0.0001 <= x < 0 else x),
)
config = Config()
client = Client(
    config.binance_key, config.binance_secret, {"verify": False, "timeout": 20}
)
extraRows = ["Lost", "Withdraws"]
shownRows = ["BTC", "ETH", "LTC", "USDT", "BNB"]
tradefees = 0.00075
logging.basicConfig(
    filename="./portfolio-data/history.log",
    format="%(message)s",
    # datefmt="%d-%m-%Y %H:%M:%S",
    filemode="a",
    level=logging.DEBUG,
    force=True,
    encoding="utf-8",
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
try:
    with open("./portfolio-data/prices.json", "r") as openfile:
        prices = json.load(openfile)
except FileNotFoundError:
    prices = {}

logging.info("")
logging.info("=" * 50)
logging.info((" " * 15) + datetime.now().strftime("%d-%m-%Y %H:%M:%S"))
logging.info("=" * 50)


def log(string="", error=False):
    print(string)
    if error:
        logging.error("ERROR: " + str(string))
    else:
        logging.info(string)


def fullPrint(df):
    with pd.option_context("display.max_rows", None, "display.max_columns", None):
        log(df)


def getPriceAtTime(symbol: str, endTime=time.time() * 1000, save=False):
    if symbol in ["USDT", *extraRows]:
        return 1
    if "timestamp" in dir(endTime):
        endTime = endTime.timestamp() * 1000
    endTime = int(endTime)
    if symbol in prices and str(endTime) in prices[symbol]:
        return prices[symbol][str(endTime)]
    # ['Open Time', 'Open', 'High', 'Low', 'Close', 'Volume', 'Close Time', '', '', '', '', '']
    res = client.get_klines(
        symbol=symbol + "USDT",
        interval="1m",
        limit=1,
        endTime=endTime,
    )
    if len(res) == 0:
        log(f"{symbol} {endTime} {res}", error=False)
    if save:
        # log(f"Saving price for {symbol} at {endTime}")
        if not symbol in prices:
            prices[symbol] = {}
        prices[symbol][str(endTime)] = float(res[0][4])
    return float(res[0][4])


def getBalances():
    accountInfo = client.get_account()
    balances = pd.DataFrame(accountInfo["balances"])
    balances[["free", "locked"]] = balances[["free", "locked"]].apply(pd.to_numeric)
    balances = balances[(balances["free"] > 0) | (balances["locked"] > 0)]
    balances["total"] = balances["free"] + balances["locked"]
    return balances


def calculateUSD(row):
    return row["Amount"] * getPriceAtTime(row.name)


def main():
    p2p: DataFrame = pd.read_json("./portfolio-data/p2p.json", convert_dates=["Time"])
    p2pSum = p2p.sum(0)

    # converts: DataFrame = pd.read_json(
    #     "./portfolio-data/converts.json", convert_dates=["Time"]
    # )
    balances = getBalances()
    allSymbols = np.unique(
        np.concatenate(
            (
                p2p["Symbol"],
                # converts["To Symbol"],
                balances["asset"],
                extraRows,
            ),
        )
    )
    zeros = np.zeros_like(allSymbols, float)
    portfolio = pd.DataFrame(
        # {key: zeros for key in ["Amount", "USDT"]}, index=allSymbols
        {key: zeros for key in ["Amount"]},
        index=allSymbols,
    )

    # # P2P coins
    # for i, v in p2p.iterrows():
    #     portfolio.loc[v["Symbol"]]["USDT"] += v["USDT"]
    #     portfolio.loc[v["Symbol"]]["Amount"] += v["USDT"] / v["Bought At"]

    # # Converts coins
    # for i, v in converts.iterrows():

    #     if v["From Symbol"] in allSymbols:
    #         portfolio.loc[v["From Symbol"]]["Amount"] -= v["From Amount"]
    #         portfolio.loc[v["From Symbol"]]["USDT"] -= v["From Amount"] * getPriceAtTime(
    #             v["From Symbol"], v["Time"], True
    #         )

    #     if v["To Symbol"] in allSymbols:
    #         portfolio.loc[v["To Symbol"]]["Amount"] += v["To Amount"]

    #         portfolio.loc[v["To Symbol"]]["USDT"] += v["To Amount"] * getPriceAtTime(
    #             v["To Symbol"], v["Time"], True
    #     )

    # Balances coins
    balanceSum = 0
    for symbol in portfolio.index:
        balance = balances.loc[balances["asset"] == symbol]
        if len(balance) == 0:
            continue
        total = balance["total"].values[0]
        if symbol in extraRows or symbol == "USDT":
            balanceSum += total
            continue
        usd = total * getPriceAtTime(symbol)
        portfolio.loc[symbol]["Amount"] = total
        portfolio.loc[symbol]["USDT"] = usd
        balanceSum += usd

    # Final touches
    portfolio.loc["Withdraws"]["USDT"] = 80
    portfolio.loc["Lost"]["USDT"] = 200
    # if shownRows:
    #     portfolio = portfolio.filter(items=[*extraRows, *shownRows], axis=0)
    portfolio = portfolio.filter(items=[], axis=0)
    # portfolio = portfolio.sort_values("USDT", ascending=False)
    # portfolio.loc[""] = ''
    # portfolio.loc["P2P In"] = [p2pSum["USDT"], '']
    # portfolio.loc["Balance"] = [balanceSum, '']
    # portfolio.loc["Profit"] = [balanceSum-p2pSum["USDT"], '']
    portfolio.loc["P2P In"] = p2pSum["USDT"]
    portfolio.loc["Balance"] = balanceSum
    portfolio.loc["Profit"] = balanceSum - p2pSum["USDT"]
    fullPrint(portfolio)

    json_object = json.dumps(prices, indent=4)
    with open("./portfolio-data/prices.json", "w+") as outfile:
        outfile.write(json_object)


def getAllOrders(symbol: str) -> DataFrame:
    resp = None
    try:
        df = pd.read_json(f"./portfolio-data/history/{symbol}.json")
    except:
        df = pd.DataFrame()
    if len(df) == 0:
        while True:
            try:
                resp = client.get_all_orders(symbol=symbol + "USDT", limit=1000)
            except BinanceAPIException as e:
                print(e)
                time.sleep(3)
                continue
            break
        df = pd.DataFrame(resp)
    while resp is None or len(resp) > 0:
        while True:
            try:
                resp = client.get_all_orders(
                    symbol=symbol + "USDT", endTime=df.loc[0]["time"] - 1, limit=1000
                )
            except BinanceAPIException as e:
                print(e)
                time.sleep(3)
                continue
            break
        df = df.append(pd.DataFrame(resp))
        df.sort_values(by=["time"], inplace=True)
        df.reset_index(inplace=True, drop=True)
    if len(df) == 0:
        return df

    df = df[
        ["executedQty", "cummulativeQuoteQty", "side", "updateTime", "time", "price"]
    ]
    df[["executedQty", "cummulativeQuoteQty", "price"]] = df[
        ["executedQty", "cummulativeQuoteQty", "price"]
    ].apply(pd.to_numeric)
    df = df[df["executedQty"] > 0]
    df.reset_index(inplace=True, drop=True)
    df.to_json(f"./portfolio-data/history/{symbol}.json")
    return df


def mainX():
    # t = client.get_server_time()["serverTime"] // 1000
    # print(t, datetime.utcfromtimestamp(int(t)))
    # t = time.time()
    # print(int(t), datetime.utcfromtimestamp(int(t)))

    orders = getAllOrders("SAND")

    with open("x.csv", "w") as f:
        for i, order in orders.iterrows():
            side = order["side"][0] + order["side"][1:].lower()
            # price = getPriceAtTime("SAND", endTime=order["time"])
            f.write(
                f"{side},{order['price']},{order['executedQty']},{order['cummulativeQuoteQty']}\n"
            )


if __name__ == "__main__":
    main()
