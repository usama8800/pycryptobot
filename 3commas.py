from config import Config
import sys
import time

import numpy as np
import pandas as pd
from binance.client import Client
from py3cw.request import Py3CW
from tqdm import tqdm

config = Config()
client = Client(
    config.binance_key, config.binance_secret, {"verify": False, "timeout": 20}
)
p3cw = Py3CW(
    key=config.tc_key,
    secret=config.tc_secret,
    request_options={
        "request_timeout": 10,
        "nr_of_retries": 1,
        "retry_status_codes": [502],
    },
)


def getPriceAtTime(symbol: str, endTime=time.time() * 1000, save=False):
    if "timestamp" in dir(endTime):
        endTime = endTime.timestamp() * 1000
    endTime = int(endTime)
    # ['Open Time', 'Open', 'High', 'Low', 'Close', 'Volume', 'Close Time', '', '', '', '', '']
    res = client.get_klines(
        symbol=symbol,
        interval="1m",
        limit=1,
        endTime=endTime,
    )
    return float(res[0][4])


def getBalances():
    accountInfo = client.get_account()
    balances = pd.DataFrame(accountInfo["balances"])
    balances[["free", "locked"]] = balances[["free", "locked"]].apply(pd.to_numeric)
    balances = balances[(balances["free"] > 0) | (balances["locked"] > 0)]
    balances["total"] = balances["free"] + balances["locked"]
    return balances


def getNeededUSDTFromSettings(
    baseOrder, safetyOrderSize, maxSafetyOrders, safetyOrderVolumeDeviation
):
    return (1 - safetyOrderVolumeDeviation ** maxSafetyOrders) / (
        1 - safetyOrderVolumeDeviation
    ) * safetyOrderSize + baseOrder


def getLowestTPPercentFromSettings(
    safetyOrderSize,
    maxSafetyOrders,
    safetyOrderVolumeDeviation,
    safetyOrderStep,
    takeProfit,
):
    prices = [100 - safetyOrderStep * i for i in range(0, maxSafetyOrders + 1)]
    volumes = [
        10,
        *[
            safetyOrderSize * safetyOrderVolumeDeviation ** i
            for i in range(0, maxSafetyOrders)
        ],
    ]
    avgPrice = np.dot(prices, volumes) / sum(volumes)
    tpPrice = avgPrice * (1 + takeProfit / 100)
    tpPercent = tpPrice - 100
    return tpPercent


def printSafetys(
    baseOrder,
    safetyOrderSize,
    maxSafetyOrders,
    safetyOrderVolumeDeviation,
    safetyOrderPriceDeviation,
    takeProfit,
):
    originalPrice = 100
    currentPrice = originalPrice
    buyPrices = [currentPrice]
    avgBuyPrice = currentPrice
    needed = baseOrder + safetyOrderSize
    buyVolumes = [baseOrder]
    boughtCoins = baseOrder / currentPrice
    df = pd.DataFrame(
        {
            "Price": pd.Series([], dtype="float"),
            "Volume": pd.Series([], dtype="float"),
            "Avg Price": pd.Series([], dtype="float"),
            "TP Price": pd.Series([], dtype="float"),
            "TP %": pd.Series([], dtype="float"),
            "TP $": pd.Series([], dtype="float"),
        }
    )
    tpPrice = avgBuyPrice * (1 + takeProfit / 100)
    df.loc[0] = [
        currentPrice,
        baseOrder,
        avgBuyPrice,
        tpPrice,
        (tpPrice - originalPrice) / originalPrice * 100,
        boughtCoins * tpPrice - baseOrder,
    ]
    for i in range(maxSafetyOrders):
        if i != 0:
            safetyOrderSize *= safetyOrderVolumeDeviation
            needed += safetyOrderSize
        currentPrice -= originalPrice * safetyOrderPriceDeviation / 100
        buyPrices.append(currentPrice)
        buyVolumes.append(safetyOrderSize)
        avgBuyPrice = np.dot(buyPrices, buyVolumes) / sum(buyVolumes)
        tpPrice = avgBuyPrice * (1 + takeProfit / 100)
        boughtCoins += safetyOrderSize / currentPrice
        df.loc[i + 1] = [
            currentPrice,
            safetyOrderSize,
            avgBuyPrice,
            tpPrice,
            (tpPrice - originalPrice) / originalPrice * 100,
            boughtCoins * tpPrice - sum(buyVolumes),
        ]
    print(df)


def getBestBotSettings(usdt):
    bestSettings = (0, 0, 0, 0, 0, 0)
    for baseOrder in range(10, 11):
        for safetyOrderSize in np.arange(baseOrder, baseOrder * 5, 0.1):
            for safetyOrderDeviation in np.arange(1.01, 1.5, 0.01):
                for safetyOrderStep in np.arange(1.1, 10, 0.01):
                    for maxSafetyOrders in range(15, 101):
                        neededUSDT = getNeededUSDTFromSettings(
                            baseOrder,
                            safetyOrderSize,
                            maxSafetyOrders,
                            safetyOrderDeviation,
                        )
                        if neededUSDT > usdt:
                            break
                        lowestTPPercent = getLowestTPPercentFromSettings(
                            safetyOrderSize,
                            maxSafetyOrders,
                            safetyOrderDeviation,
                            safetyOrderStep,
                            1.5,
                        )
                        if neededUSDT > bestSettings[4] and lowestTPPercent <= -18:
                            bestSettings = (
                                baseOrder,
                                safetyOrderSize,
                                maxSafetyOrders,
                                safetyOrderDeviation,
                                neededUSDT,
                                safetyOrderStep,
                            )
                            # print(f"{baseOrder} {safetyOrderSize:.2f} {safetyOrderDeviation:.2f} {safetyOrderStep:.2f} {maxSafetyOrders} {neededUSDT:.2f}")
                    if maxSafetyOrders != 99:
                        break
    return bestSettings


def main(live=False, totalUSDT=None, safetys=False, needed=False):
    resp = p3cw.request(entity="bots", action="")
    bots = pd.DataFrame(resp[1])
    bots = bots[bots["name"] == "TA_COMPOSITE"]
    bots = bots.reset_index()
    bots[
        [
            "base_order_volume",
            "safety_order_volume",
            "martingale_volume_coefficient",
            "safety_order_step_percentage",
            "take_profit",
        ]
    ] = bots[
        [
            "base_order_volume",
            "safety_order_volume",
            "martingale_volume_coefficient",
            "safety_order_step_percentage",
            "take_profit",
        ]
    ].apply(
        pd.to_numeric
    )
    bot = bots.loc[0]
    divideInto = len(bot["pairs"])

    if needed:
        needed = getNeededUSDTFromSettings(
            bot["base_order_volume"],
            bot["safety_order_volume"],
            bot["max_safety_orders"],
            bot["martingale_volume_coefficient"],
        )
        print(f"{needed*divideInto:.2f}")
        return

    if safetys:
        printSafetys(
            bot["base_order_volume"],
            bot["safety_order_volume"],
            bot["max_safety_orders"],
            bot["martingale_volume_coefficient"],
            bot["safety_order_step_percentage"],
            bot["take_profit"],
        )
        return

    if totalUSDT is None:
        balances = getBalances()
        totalUSDT = 0
        resp = p3cw.request(
            entity="deals", action="", payload={"bot_id": bot["id"], "scope": "active"}
        )
        deals = pd.DataFrame(resp[1])
        deals = deals[["pair", "bought_amount", "bought_volume"]]

        for pair in bot["pairs"]:
            totalUSDT += float(deals[deals["pair"] == pair]["bought_volume"].values[0])

        totalUSDT += balances[balances["asset"] == "USDT"]["total"].values[0]
    (
        baseOrder,
        safetyOrderSize,
        maxSafetyOrders,
        safetyOrderDeviation,
        neededUSDT,
        safetyOrderStep,
    ) = getBestBotSettings((totalUSDT + 100) / divideInto)
    if baseOrder == 0:
        print(f"Total USDT: {totalUSDT:.2f}")
        print("Unfeasable")
        return

    printSafetys(
        baseOrder,
        safetyOrderSize,
        maxSafetyOrders,
        safetyOrderDeviation,
        safetyOrderStep,
        bot["take_profit"],
    )
    print()
    print(
        f"""Bot "{bot['name']}" settings
Base Order:             {baseOrder}
Safety Order Size:      {safetyOrderSize:.2f}
Safety Order Variation: {safetyOrderDeviation:.2f}
Safety Order Step:      {safetyOrderStep:.2f}
Max Safety Order:       {maxSafetyOrders}
Using $ / bot:          {neededUSDT:.2f}
Using $:                {neededUSDT*divideInto:.2f} / {totalUSDT:.2f}"""
    )

    if (
        abs(bot["base_order_volume"] - baseOrder) < 1
        and abs(bot["safety_order_volume"] - safetyOrderSize) < 0.1
        and abs(bot["martingale_volume_coefficient"] - safetyOrderDeviation) < 0.1
        and abs(bot["max_safety_orders"] - maxSafetyOrders) < 1
        or not live
    ):
        return
    return
    resp = p3cw.request(
        entity="bots",
        action="update",
        action_id=str(bot["id"]),
        payload={
            "name": bot["name"],
            "pairs": bot["pairs"],
            "max_active_deals": 1,
            "base_order_volume": baseOrder,
            "take_profit": bot["take_profit"],
            "safety_order_volume": safetyOrderSize,
            "martingale_volume_coefficient": safetyOrderDeviation,
            "martingale_step_coefficient": 1,
            "max_safety_orders": maxSafetyOrders,
            "active_safety_orders_count": 1,
            "safety_order_step_percentage": bot["safety_order_step_percentage"],
            "take_profit_type": bot["take_profit_type"],
            "strategy_list": bot["strategy_list"],
            "bot_id": int(bot["id"]),
        },
    )
    if len(resp[0].keys()) == 0:
        print("Success")
        app.notifyTelegram(
            f"""* Bot "{bot['name']}" updated*
```
Base Order:             {baseOrder}
Safety Order Size:      {safetyOrderSize:.2f}
Safety Order Variation: {safetyOrderDeviation:.2f}
Max Safety Order:       {maxSafetyOrders}
Using $:                {neededUSDT:.2f} / {totalUSDT:.2f}
```""",
            False,
        )


if __name__ == "__main__":
    args = sys.argv[1:]
    live = False
    usdt = None
    safetys = False
    needed = False

    for arg in args:
        if arg.startswith("--live"):
            live = int(arg[7:]) == 1
        elif arg.startswith("--usdt"):
            usdt = int(arg[7:], 10)
        elif arg in ["--safetys", "--safeteys"]:
            safetys = True
        elif arg == "--needed":
            needed = True
    main(live, usdt, safetys, needed)
