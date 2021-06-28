from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from binance.client import Client

from models.exchange.coinbase_pro.api import (FREQUENCY_EQUIVALENTS,
                                              SUPPORTED_GRANULARITY)
from models.PyCryptoBot import PyCryptoBot, to_binance_granularity

market = "DOGEUSDT"
baseOrderVolume = 10
firstSafetyOrderVolume = 22.5
safetyOrderPriceDeviation = 0.98
safetyOrderVolumeDeviation = 1.05
maxSafetyOrders = 15
takeProfitPercentage = 1.015

app = PyCryptoBot()
client = Client(app.getAPIKey(), app.getAPISecret(), {"verify": False, "timeout": 20})


def main():
    tradingData = getTradingData()
    originalFunds = getNeededUSDTFromSettings(
        baseOrderVolume, firstSafetyOrderVolume, maxSafetyOrders, safetyOrderVolumeDeviation
    )
    funds = originalFunds
    coin = 0

    currentBuyPrices = []
    nextSafetyOrderPrice = 0
    takeProfitOrderPrice = 0
    for _, row in tradingData.iterrows():
        print(_)
        if len(currentBuyPrices) == 0: # First row
            coin = baseOrderVolume / row['open']
            funds -= baseOrderVolume
            currentBuyPrices = [row['open']]
            avgBuyPrice = row['open']
            nextSafetyOrderPrice = row['open'] * safetyOrderPriceDeviation
            nextSafetyOrderVolume = firstSafetyOrderVolume
            takeProfitOrderPrice = avgBuyPrice * takeProfitPercentage
        if row['low'] <= nextSafetyOrderPrice:
            coin += nextSafetyOrderVolume / nextSafetyOrderPrice
            funds -= nextSafetyOrderVolume
            currentBuyPrices.append(nextSafetyOrderPrice)
            avgBuyPrice = sum(currentBuyPrices)/len(currentBuyPrices)
            nextSafetyOrderPrice *= safetyOrderPriceDeviation
            nextSafetyOrderVolume *= safetyOrderVolumeDeviation
            takeProfitOrderPrice = avgBuyPrice * takeProfitPercentage
        if row['high'] >= takeProfitOrderPrice:
            funds += coin * takeProfitOrderPrice
            coin = baseOrderVolume / takeProfitOrderPrice
            funds -= baseOrderVolume
            currentBuyPrices = [takeProfitOrderPrice]
            avgBuyPrice = takeProfitOrderPrice
            nextSafetyOrderPrice = takeProfitOrderPrice * safetyOrderPriceDeviation
            nextSafetyOrderVolume = firstSafetyOrderVolume
            takeProfitOrderPrice = avgBuyPrice * takeProfitPercentage
    print(funds, coin, funds+coin*row['close'])


def getNeededUSDTFromSettings(
    baseOrder, safetyOrderSize, maxSafetyOrders, safetyOrderVolumeDeviation
):
    needed = baseOrder + safetyOrderSize
    for i in range(1, maxSafetyOrders):
        safetyOrderSize *= safetyOrderVolumeDeviation
        needed += safetyOrderSize
    return needed


def getTradingData():
    if app.simstartdate is not None and app.simenddate is not None:
        date = app.simstartdate.split("-")
        startDate = datetime(int(date[0]), int(date[1]), int(date[2]))
        if app.simenddate == "now":
            endDate = datetime.now()
        else:
            date = app.simenddate.split("-")
            endDate = datetime(int(date[0]), int(date[1]), int(date[2]))
    elif app.simstartdate is not None and app.simenddate is None:
        date = app.simstartdate.split("-")
        startDate = datetime(int(date[0]), int(date[1]), int(date[2]))
        endDate = startDate + timedelta(minutes=(app.getGranularity() / 60) * 300)
    elif app.simstartdate is None and app.simenddate is not None:
        if app.simenddate == "now":
            endDate = datetime.now()
        else:
            date = app.simenddate.split("-")
            endDate = datetime(int(date[0]), int(date[1]), int(date[2]))
        startDate = endDate - timedelta(minutes=(app.getGranularity() / 60) * 300)

    granularity = to_binance_granularity(app.getGranularity())
    resp = client.get_historical_klines(
        market,
        granularity,
        startDate.isoformat(timespec="milliseconds"),
        endDate.isoformat(timespec="milliseconds"),
    )
    # convert the API response into a Pandas DataFrame
    df = pd.DataFrame(
        resp,
        columns=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_asset_volume",
            "number_of_trades",
            "taker_buy_base_asset_volume",
            "traker_buy_quote_asset_volume",
            "ignore",
        ],
    )
    df["market"] = market
    df["granularity"] = granularity

    # binance epoch is too long
    df["open_time"] = df["open_time"] + 1
    df["open_time"] = df["open_time"].astype(str)
    df["open_time"] = df["open_time"].str.replace(r"\d{3}$", "", regex=True)

    try:
        freq = FREQUENCY_EQUIVALENTS[SUPPORTED_GRANULARITY.index(granularity)]
    except:
        freq = "D"

    # convert the DataFrame into a time series with the date as the index/key
    try:
        tsidx = pd.DatetimeIndex(
            pd.to_datetime(df["open_time"], unit="s"), dtype="datetime64[ns]", freq=freq
        )
        df.set_index(tsidx, inplace=True)
        df = df.drop(columns=["open_time"])
        df.index.names = ["ts"]
        df["date"] = tsidx
    except ValueError:
        tsidx = pd.DatetimeIndex(
            pd.to_datetime(df["open_time"], unit="s"), dtype="datetime64[ns]"
        )
        df.set_index(tsidx, inplace=True)
        df = df.drop(columns=["open_time"])
        df.index.names = ["ts"]
        df["date"] = tsidx

    # re-order columns
    df = df[["date", "low", "high", "open", "close"]]

    # correct column types
    df["low"] = df["low"].astype(float)
    df["high"] = df["high"].astype(float)
    df["open"] = df["open"].astype(float)
    df["close"] = df["close"].astype(float)

    # reset pandas dataframe index
    df.reset_index()
    return df


if __name__ == "__main__":
    main()
