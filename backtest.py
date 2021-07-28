from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from binance.client import Client

from config import Config


def to_binance_granularity(granularity: int) -> str:
    return {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 21600: "6h", 86400: "1d"}[
        granularity
    ]


baseCoin = "DOGE"
quoteCoin = "USDT"
market = baseCoin + quoteCoin
baseOrderVolume = 10
firstSafetyOrderVolume = 22.5
safetyOrderPriceDeviation = 0.98
safetyOrderVolumeDeviation = 1.05
maxSafetyOrders = 15
takeProfitPercentage = 1.015

config = Config()
client = Client(
    config.binance_key, config.binance_secret, {"verify": False, "timeout": 20}
)


def main():
    tradingData = getTradingData()
    originalFunds = getNeededUSDTFromSettings(
        baseOrderVolume,
        firstSafetyOrderVolume,
        maxSafetyOrders,
        safetyOrderVolumeDeviation,
    )
    funds = originalFunds
    coin = 0

    currentBuyPrices = []
    nextSafetyOrderPrice = 0
    takeProfitOrderPrice = 0
    fundsBeforeBaseOrder = funds
    for _, row in tradingData.iterrows():
        if len(currentBuyPrices) == 0:  # First row
            coin = baseOrderVolume / row["open"]
            funds -= baseOrderVolume
            print(
                f"{_}: Base order {coin} {baseCoin} for {baseOrderVolume} {quoteCoin}"
            )
            currentBuyPrices = [row["open"]]
            avgBuyPrice = row["open"]
            nextSafetyOrderPrice = row["open"] * safetyOrderPriceDeviation
            nextSafetyOrderVolume = firstSafetyOrderVolume
            takeProfitOrderPrice = avgBuyPrice * takeProfitPercentage
        if row["low"] <= nextSafetyOrderPrice:
            coin += nextSafetyOrderVolume / nextSafetyOrderPrice
            funds -= nextSafetyOrderVolume
            print(
                f"{_}: Safety order {nextSafetyOrderVolume / nextSafetyOrderPrice} {baseCoin} for {nextSafetyOrderVolume} {quoteCoin}"
            )
            currentBuyPrices.append(nextSafetyOrderPrice)
            avgBuyPrice = sum(currentBuyPrices) / len(currentBuyPrices)
            nextSafetyOrderPrice *= safetyOrderPriceDeviation
            nextSafetyOrderVolume *= safetyOrderVolumeDeviation
            takeProfitOrderPrice = avgBuyPrice * takeProfitPercentage
        if row["high"] >= takeProfitOrderPrice:
            funds += coin * takeProfitOrderPrice
            print(f"{_}: Take profit {funds-fundsBeforeBaseOrder} {quoteCoin}")
            fundsBeforeBaseOrder = funds
            funds -= baseOrderVolume
            coin = baseOrderVolume / takeProfitOrderPrice
            print(
                f"{_}: Base order {coin} {baseCoin} for {baseOrderVolume} {quoteCoin}"
            )
            currentBuyPrices = [takeProfitOrderPrice]
            avgBuyPrice = takeProfitOrderPrice
            nextSafetyOrderPrice = takeProfitOrderPrice * safetyOrderPriceDeviation
            nextSafetyOrderVolume = firstSafetyOrderVolume
            takeProfitOrderPrice = avgBuyPrice * takeProfitPercentage
    endFunds = fundsBeforeBaseOrder
    print(
        f"{endFunds:.2f} - {originalFunds:.2f} = {endFunds-originalFunds:+.2f} {quoteCoin}"
    )


def getNeededUSDTFromSettings(
    baseOrder, safetyOrderSize, maxSafetyOrders, safetyOrderVolumeDeviation
):
    needed = baseOrder + safetyOrderSize
    for i in range(1, maxSafetyOrders):
        safetyOrderSize *= safetyOrderVolumeDeviation
        needed += safetyOrderSize
    return needed


def getTradingData():
    if config.simstartdate is not None and config.simenddate is not None:
        date = config.simstartdate.split("-")
        startDate = datetime(int(date[0]), int(date[1]), int(date[2]))
        if config.simenddate == "now":
            endDate = datetime.now()
        else:
            date = config.simenddate.split("-")
            endDate = datetime(int(date[0]), int(date[1]), int(date[2]))
    elif config.simstartdate is not None and config.simenddate is None:
        date = config.simstartdate.split("-")
        startDate = datetime(int(date[0]), int(date[1]), int(date[2]))
        endDate = startDate + timedelta(minutes=(config.granularity / 60) * 300)
    elif config.simstartdate is None and config.simenddate is not None:
        if config.simenddate == "now":
            endDate = datetime.now()
        else:
            date = config.simenddate.split("-")
            endDate = datetime(int(date[0]), int(date[1]), int(date[2]))
        startDate = endDate - timedelta(minutes=(config.granularity / 60) * 300)
    else:
        raise KeyError("Set start and end date")

    granularity = to_binance_granularity(config.getGranularity())
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

    SUPPORTED_GRANULARITY = [60, 300, 900, 3600, 21600, 86400]
    FREQUENCY_EQUIVALENTS = ["T", "5T", "15T", "H", "6H", "D"]

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
