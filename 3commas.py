import math
import sys
import time

import numpy as np
import pandas as pd
from binance.client import Client
from py3cw.request import Py3CW

from models.PyCryptoBot import PyCryptoBot

app = PyCryptoBot()
client = Client(app.getAPIKey(), app.getAPISecret(), {"verify": False, "timeout": 20})
p3cw = Py3CW(
    key=app.tc_key,
    secret=app.tc_secret,
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
    needed = baseOrder + safetyOrderSize
    for i in range(1, maxSafetyOrders):
        safetyOrderSize *= safetyOrderVolumeDeviation
        needed += safetyOrderSize
    return needed


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
        currentPrice = originalPrice * (1 - safetyOrderPriceDeviation * (i+1) / 100)
        buyPrices.append(currentPrice)
        buyVolumes.append(safetyOrderSize)
        avgBuyPrice = sum([v * w for v, w in zip(buyPrices, buyVolumes)]) / sum(
            buyVolumes
        )
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


def getBestBotSettings(usdt, maxSafetyOrders):
    bestSettings = (0, 0, 0, 0, 0)
    for baseOrder in range(10, 11):
        for safetyOrderSize in np.arange(baseOrder, baseOrder * 5, 0.1):
            for safetyOrderDeviation in np.arange(1.01, 1.5, 0.01):
                neededUSDT = getNeededUSDTFromSettings(
                    baseOrder, safetyOrderSize, maxSafetyOrders, safetyOrderDeviation
                )
                if neededUSDT > usdt:
                    break
                if neededUSDT > bestSettings[-1]:
                    bestSettings = (
                        baseOrder,
                        safetyOrderSize,
                        maxSafetyOrders,
                        safetyOrderDeviation,
                        neededUSDT,
                    )
    return bestSettings


def main(live=False, totalUSDT=None, safetys=False):
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
    ) = getBestBotSettings((totalUSDT+100) / divideInto, bot["max_safety_orders"])
    if baseOrder == 0:
        print(f"Total USDT: {totalUSDT:.2f}")
        print("Unfeasable")
        return

    printSafetys(
        baseOrder,
        safetyOrderSize,
        maxSafetyOrders,
        safetyOrderDeviation,
        bot["safety_order_step_percentage"],
        bot["take_profit"],
    )
    print()
    print(
        f"""Bot "{bot['name']}" settings
Base Order:             {baseOrder}
Safety Order Size:      {safetyOrderSize:.2f}
Safety Order Variation: {safetyOrderDeviation:.2f}
Max Safety Order:       {maxSafetyOrders}
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

    for arg in args:
        if arg.startswith("--live"):
            live = int(arg[7:]) == 1
        elif arg.startswith("--usdt"):
            usdt = int(arg[7:], 10)
        elif arg in ["--safetys", "--safeteys"]:
            safetys = True
    main(live, usdt, safetys)
