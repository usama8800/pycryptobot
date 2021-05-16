import time
from typing import Set

import mezmorize
import numpy as np
import pandas as pd
from binance.client import Client
from pandas.core.frame import DataFrame

from models.PyCryptoBot import PyCryptoBot

pd.set_option(
    "display.float_format",
    lambda x: ("%.0f" if int(x) == x else "%0.0f" if abs(x) < 0.0001 else "%.4f")
    % (-x if -0.0001 <= x < 0 else x),
)
app = PyCryptoBot()
client = Client(app.getAPIKey(), app.getAPISecret(), {"verify": False, "timeout": 20})
cache = mezmorize.Cache(
    CACHE_TYPE="filesystem", CACHE_DIR="cache", CACHE_DEFAULT_TIMEOUT=3600
)
extraRows = ["Fees", "Lost", "Withdraws"]
tradefees = 0.00075


def fullPrint(df):
    with pd.option_context("display.max_rows", None, "display.max_columns", None):
        print(df)


@cache.memoize()
def getPriceAtTime(symbol: str, endTime=time.time() * 1000 - 10000):
    if symbol in ["USDT", *extraRows]:
        return 1
    if "timestamp" in dir(endTime):
        endTime = endTime.timestamp()
        endTime = endTime * 1000
    endTime = int(endTime)
    # ['Open Time', 'Open', 'High', 'Low', 'Close', 'Volume', 'Close Time', '', '', '', '', '']
    res = client.get_klines(
        symbol=symbol + "USDT",
        interval="1m",
        limit=1,
        endTime=endTime,
    )
    if len(res) == 0:
        print(symbol, endTime, res)
    return float(res[0][1])


@cache.memoize()
def getAllOrders(symbol: str) -> DataFrame:
    resp = client.get_all_orders(symbol=symbol + "USDT", limit=1000)
    df = pd.DataFrame(resp)
    while len(resp) > 0:
        resp = client.get_all_orders(
            symbol=symbol + "USDT", endTime=resp[0]["time"] - 1, limit=1000
        )
        df = df.append(pd.DataFrame(resp))
    if len(df) == 0:
        return df

    df = df[(df["status"] != "CANCELED") & (df["status"] != "EXPIRED")]
    df = df[["executedQty", "cummulativeQuoteQty", "status", "side", "updateTime"]]
    df[["executedQty", "cummulativeQuoteQty"]] = df[
        ["executedQty", "cummulativeQuoteQty"]
    ].apply(pd.to_numeric)
    return df


@cache.memoize()
def getBalances():
    accountInfo = client.get_account()
    balances = pd.DataFrame(accountInfo["balances"])
    balances[["free", "locked"]] = balances[["free", "locked"]].apply(pd.to_numeric)
    balances = balances[(balances["free"] > 0) | (balances["locked"] > 0)]
    return balances


@cache.memoize()
def getWithdraws():
    hist = client.get_withdraw_history(status=6, limit=1000)
    df = pd.DataFrame(hist["withdrawList"])
    while len(hist["withdrawList"]) > 0:
        hist = client.get_withdraw_history(
            status=6,
            limit=1000,
            endTime=hist["withdrawList"][-1]["applyTime"] - 1,
        )
        df = df.append(pd.DataFrame(hist["withdrawList"]))
    return df


@cache.memoize(unless=lambda: True)
def getDusts():
    dusts = client.get_dust_log()
    df = pd.DataFrame(dusts["results"]["rows"][0]["logs"])
    # print(dusts["results"]["rows"][0]["logs"]["operateTime"])
    # while len(dusts["results"]["rows"]) > 0:
    #     # TODO Confirm 0 or -1 for earliest time
    #     dusts = client.get_dust_log(
    #         endTime=dusts["results"]["rows"][0]["logs"][0]["operateTime"] - 1
    #     )
    #     df = df.append(pd.DataFrame(dusts))
    df[["amount", "transferedAmount", "serviceChargeAmount"]] = df[
        ["amount", "transferedAmount", "serviceChargeAmount"]
    ].apply(pd.to_numeric)
    df[["operateTime"]] = df[["operateTime"]].apply(pd.to_datetime)
    return df


def calculateUSD(row):
    return row["Amount"] * getPriceAtTime(row.name)


def calculateProfit(row):
    return row["USD Out"] - row["USD In"]


def main():
    p2p: DataFrame = pd.read_json("./portfolio-data/p2p.json", convert_dates=["Time"])
    converts: DataFrame = pd.read_json(
        "./portfolio-data/converts.json", convert_dates=["Time"]
    )
    balances = getBalances()
    allSymbols = np.unique(
        np.concatenate(
            (
                p2p["Symbol"].unique(),
                converts["To Symbol"].unique(),
                balances["asset"],
                extraRows,
            ),
        )
    )
    zeros = np.zeros_like(allSymbols)
    portfolio = pd.DataFrame(
        {key: zeros for key in ["USD In", "Amount"]}, index=allSymbols
    )

    for i, v in p2p.iterrows():
        portfolio.loc[v["Symbol"]]["USD In"] += v["USD In"]
        portfolio.loc[v["Symbol"]]["Amount"] += v["USD In"] / v["Bought At"]
    for i, v in converts.iterrows():
        portfolio.loc[v["From Symbol"]]["Amount"] -= v["From Amount"]
        portfolio.loc[v["To Symbol"]]["Amount"] += v["To Amount"]

        portfolio.loc[v["From Symbol"]]["USD In"] -= v["From Amount"] * getPriceAtTime(
            v["From Symbol"], v["Time"]
        )
        portfolio.loc[v["To Symbol"]]["USD In"] += v["To Amount"] * getPriceAtTime(
            v["To Symbol"], v["Time"]
        )

    for symbol in portfolio.index:
        if symbol in extraRows or symbol == "USDT":
            continue

        orders = getAllOrders(symbol)
        if len(orders) == 0:
            continue
        for i, v in orders.iterrows():
            if v["side"] == "BUY":
                portfolio.loc[symbol]["USD In"] += v["cummulativeQuoteQty"]
                portfolio.loc[symbol]["Amount"] += v["executedQty"]
                portfolio.loc["USDT"]["USD In"] -= v["cummulativeQuoteQty"]
                portfolio.loc["USDT"]["Amount"] -= v["cummulativeQuoteQty"]
            else:
                portfolio.loc[symbol]["USD In"] -= v["cummulativeQuoteQty"]
                portfolio.loc[symbol]["Amount"] -= v["executedQty"]
                portfolio.loc["USDT"]["USD In"] += v["cummulativeQuoteQty"]
                portfolio.loc["USDT"]["Amount"] += v["cummulativeQuoteQty"]
            portfolio.loc["Fees"]["USD In"] += v["cummulativeQuoteQty"] * tradefees
            portfolio.loc["BNB"]["USD In"] -= v["cummulativeQuoteQty"] * tradefees
            portfolio.loc["BNB"]["Amount"] -= (
                v["cummulativeQuoteQty"]
                * tradefees
                / getPriceAtTime("BNB", v["updateTime"])
            )

    for i, withdraw in getWithdraws().iterrows():
        price = getPriceAtTime(withdraw["asset"], withdraw["applyTime"])
        portfolio.loc[withdraw["asset"]]["Amount"] -= withdraw["amount"]
        portfolio.loc[withdraw["asset"]]["USD In"] -= withdraw["amount"] * price
        portfolio.loc["Fees"]["USD In"] += withdraw["transactionFee"]
        portfolio.loc["Withdraws"]["USD In"] += withdraw["amount"] * price

    # for i, deposit in getDeposits().iterrows():
    #     price

    for i, dust in getDusts().iterrows():
        portfolio.loc[dust["fromAsset"]]["Amount"] -= dust["amount"]
        portfolio.loc[dust["fromAsset"]]["USD In"] -= dust["amount"] * getPriceAtTime(
            dust["fromAsset"], dust["operateTime"]
        )
        portfolio.loc["BNB"]["Amount"] += dust["transferedAmount"]
        portfolio.loc["BNB"]["USD In"] += dust["transferedAmount"] * getPriceAtTime(
            "BNB", dust["operateTime"]
        )
        portfolio.loc["Fees"]["USD In"] += dust["serviceChargeAmount"] * getPriceAtTime(
            "BNB", dust["operateTime"]
        )

    portfolio.loc["Lost"]["USD In"] = 196.2
    portfolio["USD Out"] = portfolio.apply(calculateUSD, axis=1)
    portfolio["Profit"] = portfolio.apply(calculateProfit, axis=1)
    portfolio = portfolio.sort_values("Profit", ascending=False)
    sums = portfolio.sum(0)
    fullPrint(portfolio)
    print()
    print("USD Out", sums["USD Out"])
    print("Actual Profit", sums["USD Out"] - sums["USD In"])
    print(
        "Binance Profit",
        sums["USD Out"]  # What I get
        - p2p.sum(0)["USD In"]  # What I put in (through P2P)
        + portfolio.loc["USDT"][
            "Profit"
        ]  # What I lost in P2P due to higher rates. Value is negative so adding
        + portfolio.loc["Withdraws"]["USD In"],
    )


if __name__ == "__main__":
    main()
