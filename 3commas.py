import sys
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
from binance.client import Client
from py3cw.request import Py3CW
from tqdm import tqdm

from config import Config

config = Config()
p3cw = Py3CW(
    key=config.tc_key,
    secret=config.tc_secret,
    request_options={
        "request_timeout": 10,
        "nr_of_retries": 1,
        "retry_status_codes": [502],
    },
)


def getBalances():
    client = Client(
        config.binance_key, config.binance_secret, {"verify": False, "timeout": 20}
    )
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


def getBounceFromSettings(
    safetyOrder,
    maxSafetyOrders,
    safetyVariation,
    safetyStep,
    takeProfit,
):
    prices = [100 - safetyStep * i for i in range(0, maxSafetyOrders + 1)]
    if prices[-1] < 0:
        return float("inf")
    volumes = [
        10,
        *[safetyOrder * safetyVariation ** i for i in range(0, maxSafetyOrders)],
    ]
    avgPrice = np.dot(prices, volumes) / sum(volumes)
    tpPrice = avgPrice * (1 + takeProfit / 100)
    return tpPrice - 100


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


def printProfits(pairs, days=30):
    coins = [x[5:] for x in pairs]
    df = pd.DataFrame(columns=["Base", "Profit", "First Closed"])
    success = False
    for coin in coins:
        success, deals = get_deals(coin, days)
        while not success:
            print(coin, "deals failed. Retying in a sec...")
            time.sleep(1)
            success, deals = get_deals(coin, days)
        if len(deals) == 0:
            df.loc[len(df)] = [coin, 0, ""]
            continue
        firstClosed = deals.loc[len(deals) - 1]["closed_at"]
        profit = deals["actual_profit"].sum()
        df.loc[len(df)] = [coin, profit, firstClosed[: firstClosed.index("T")]]
    df = df.sort_values(by=["Profit"])
    df.reset_index(inplace=True, drop=True)
    print(df)
    print(df["Profit"].sum())


def getBestBotSettings(usdt, givenBounce=0, minSafetys=5):
    # base order, safety order, max safetys, safety variation, used usdt, safety step, lowest tp %
    settings = []
    for baseOrder in range(10, 20):
        for safetyOrder in np.arange(baseOrder, baseOrder * 5, 0.1):
            for safetyVariation in np.arange(1.01, 1.05, 0.01):
                for safetyStep in np.arange(1.1, 2.4, 0.01):
                    for maxSafetyOrders in range(minSafetys, 101):
                        neededUSDT = getNeededUSDTFromSettings(
                            baseOrder,
                            safetyOrder,
                            maxSafetyOrders,
                            safetyVariation,
                        )
                        if neededUSDT > usdt:
                            break
                        bounce = getBounceFromSettings(
                            safetyOrder,
                            maxSafetyOrders,
                            safetyVariation,
                            safetyStep,
                            1.5,
                        )

                        # must haves
                        if bounce > -givenBounce:
                            continue
                        # priorities
                        settings.append(
                            (
                                safetyVariation,
                                safetyOrder,
                                -bounce,
                                neededUSDT,
                                baseOrder,
                                maxSafetyOrders,
                                -safetyStep,
                            )
                        )
                    if maxSafetyOrders != 99:
                        break
    # base order, safety order, max safetys, safety variation, used usdt, safety step, lowest tp %
    settings.sort()
    settings = [(x[4], x[1], x[5], x[0], x[3], -x[6], -x[2]) for x in settings]
    # [print(setting) for setting in settings[-100:]]
    if len(settings):
        return settings[-1]
    return 0,0,0,0,0,0,0


def get_deals(coin, days=30):
    now = datetime.now()
    month_ago = now - timedelta(days=days)
    resp = p3cw.request(
        entity="deals",
        action="",
        payload={
            "limit": 1000,
            "scope": "completed",
            "from": month_ago.isoformat(),
            "base": coin,
        },
    )
    if "error" in resp and resp["error"]:
        return (False, False)
    deals = pd.DataFrame(resp[1])
    while len(resp[1]) != 0:
        resp = p3cw.request(
            entity="deals",
            action="",
            payload={
                "limit": 1000,
                "scope": "completed",
                "offset": len(deals),
                "from": month_ago.isoformat(),
                "base": coin,
            },
        )
        deals = deals.append(resp[1])
    deals.reset_index(inplace=True, drop=True)
    if len(deals) > 0:
        deals[
            ["actual_usd_profit", "final_profit", "usd_final_profit", "actual_profit"]
        ] = deals[
            ["actual_usd_profit", "final_profit", "usd_final_profit", "actual_profit"]
        ].apply(
            pd.to_numeric
        )
    return (True, deals)


def get_bot(name="TA_COMPOSITE"):
    resp = p3cw.request(
        entity="bots",
        action="",
        payload={"scope": "enabled"},
    )
    bots = pd.DataFrame(resp[1])
    bots = bots[bots["name"] == name]
    bots.reset_index(inplace=True, drop=True)
    bots[
        [
            "base_order_volume",
            "safety_order_volume",
            "martingale_volume_coefficient",
            "safety_order_step_percentage",
            "take_profit",
            "max_safety_orders"
        ]
    ] = bots[
        [
            "base_order_volume",
            "safety_order_volume",
            "martingale_volume_coefficient",
            "safety_order_step_percentage",
            "take_profit",
            "max_safety_orders"
        ]
    ].apply(
        pd.to_numeric
    )
    return bots.loc[0]


def fullPrint(df, rows=None, cols=None):
    with pd.option_context("display.max_rows", rows, "display.max_columns", cols):
        print(df)


class Main:
    def __init__(self):
        self.live = False
        self.usdt = None
        self.safetys = None
        self.needed = False
        self.extraBots = 0
        self.extraUSDT = 300
        self.auto = False
        self.profits = False
        self.days = 30
        self.extraBounce = 0
        self.bounce = 0
        self.extraSafetys = None

        for arg in sys.argv[1:]:
            if not arg.startswith("--"):
                raise KeyError("Unknown option")

            arg = arg[2:].split("=")
            key = arg[0]
            val = 1
            if len(arg) == 2:
                val = arg[1]

            if key == "live":
                self.live = int(val) == 1
            elif key == "usdt":
                self.usdt = int(val, 10)
            elif key in ["safetys", "safeteys"]:
                self.safetys = int(val) == 1
            elif key == "needed":
                self.needed = int(val) == 1
            elif key == "profits":
                self.profits = int(val) == 1
            elif key == "extra-usdt":
                self.extraUSDT = int(val)
            elif key == "extra-bots":
                self.extraBots = int(val)
            elif key == "auto":
                self.auto = int(val) == 1
                if self.safetys is None:
                    self.safetys = False
            elif key == "days":
                self.days = int(val)
            elif key == "extra-bounce":
                self.extraBounce = float(val)
            elif key == "bounce":
                self.bounce = float(val)
            elif key in ["extra-safetys", "extra-safeteys"]:
                self.extraSafetys = int(val)
            else:
                raise KeyError(f"Unknown option '{arg}'")

            if self.needed and self.safetys:
                raise KeyError(f"Options --safetys and --needed conflict")
            if self.needed and self.profits:
                raise KeyError(f"Options --profits and --needed conflict")
            if self.safetys and self.profits:
                raise KeyError(f"Options --profits and --safetys conflict")
        self.bot = get_bot()
        if not self.bounce and self.extraSafetys is None:
            self.bounce = int(-getBounceFromSettings(
                self.bot['safety_order_volume'],
                self.bot['max_safety_orders'],
                self.bot['martingale_volume_coefficient'],
                self.bot["safety_order_step_percentage"],
                self.bot["take_profit"],
            )) + self.extraBounce
        self.main()

    def setTotalUSDT(self):
        if self.usdt is None:
            balances = getBalances()
            self.usdt = balances[balances["asset"] == "USDT"]["total"].values[0]
            resp = p3cw.request(
                entity="deals",
                action="",
                payload={"bot_id": self.bot["id"], "scope": "active"},
            )
            deals = pd.DataFrame(resp[1])
            deals = deals[["pair", "bought_volume", "take_profit"]]

            for i in range(len(deals)):
                deal = deals.loc[i]
                self.usdt += float(deal['bought_volume']) * (1 + float(deal['take_profit'])/100)

    def main(self):
        divideInto = len(self.bot["pairs"]) + self.extraBots

        if self.needed:
            needed = getNeededUSDTFromSettings(
                self.bot["base_order_volume"],
                self.bot["safety_order_volume"],
                self.bot["max_safety_orders"],
                self.bot["martingale_volume_coefficient"],
            )
            print(f"{needed*divideInto:.2f}")
            return

        if self.safetys:
            printSafetys(
                self.bot["base_order_volume"],
                self.bot["safety_order_volume"],
                self.bot["max_safety_orders"],
                self.bot["martingale_volume_coefficient"],
                self.bot["safety_order_step_percentage"],
                self.bot["take_profit"],
            )
            return

        if self.profits:
            printProfits(self.bot["pairs"], self.days)
            return

        self.setTotalUSDT()
        minSafetys = None if self.extraSafetys is None else +self.bot["max_safety_orders"] + self.extraSafetys

        (
            baseOrder,
            safetyOrderSize,
            maxSafetyOrders,
            safetyOrderDeviation,
            neededUSDT,
            safetyOrderStep,
            lowestTPPercent,
        ) = getBestBotSettings((self.usdt + self.extraUSDT) / divideInto, self.bounce, minSafetys)
        if baseOrder == 0:
            extra = ''
            if self.extraBounce:
                extra = f" and {self.bounce} bounce"
            print(
                f"""Bot "{self.bot['name']}" settings for {divideInto} pairs
with {self.usdt:.0f} USDT{extra}

Unfeasable"""
            )
            return

        if self.safetys is None:
            printSafetys(
                baseOrder,
                safetyOrderSize,
                maxSafetyOrders,
                safetyOrderDeviation,
                safetyOrderStep,
                self.bot["take_profit"],
            )
            print()
        content = f"""
Base Order:             {baseOrder}
Safety Order Size:      {safetyOrderSize:.2f}
Safety Order Variation: {safetyOrderDeviation:.2f}
Safety Order Step:      {safetyOrderStep:.2f}
Max Safety Order:       {maxSafetyOrders}
Using $ / bot:          {neededUSDT:.2f}
Using $:                {neededUSDT*divideInto:.2f} / {self.usdt:.2f}
Lowest Take Profit %:   {lowestTPPercent:.2f}"""
        if self.auto:
            res = requests.post(
                config.discordHook,
                {
                    "content": f"""Bot `{self.bot['name']}` settings for `{divideInto}` pairs\n```{content}```"""
                },
            )
        else:
            print(f"""Bot "{self.bot['name']}" settings for {divideInto} pairs""")
            print(content)

        def cmpFloat(a, b):
            return abs(a - b) < 0.01

        allSame = (
            cmpFloat(baseOrder, float(self.bot["base_order_volume"]))
            and cmpFloat(safetyOrderSize, float(self.bot["safety_order_volume"]))
            and cmpFloat(
                safetyOrderDeviation, float(self.bot["martingale_volume_coefficient"])
            )
            and maxSafetyOrders == int(self.bot["max_safety_orders"])
            and cmpFloat(
                safetyOrderStep, float(self.bot["safety_order_step_percentage"])
            )
        )
        if not self.live or allSame:
            return
        resp = p3cw.request(
            entity="bots",
            action="update",
            action_id=str(self.bot["id"]),
            payload={
                "name": self.bot["name"],
                "pairs": self.bot["pairs"],
                "max_active_deals": int(self.bot["max_active_deals"]),
                "base_order_volume": baseOrder,
                "take_profit": self.bot["take_profit"],
                "safety_order_volume": safetyOrderSize,
                "martingale_volume_coefficient": safetyOrderDeviation,
                "martingale_step_coefficient": float(
                    self.bot["martingale_step_coefficient"]
                ),
                "max_safety_orders": maxSafetyOrders,
                "active_safety_orders_count": int(
                    self.bot["active_safety_orders_count"]
                ),
                "safety_order_step_percentage": safetyOrderStep,
                "take_profit_type": self.bot["take_profit_type"],
                "strategy_list": self.bot["strategy_list"],
                "bot_id": int(self.bot["id"]),
            },
        )
        if len(resp[0].keys()) == 0:
            if self.auto:
                extraBotText = ""
                if self.extraBots == 1:
                    extraBotText = "\nGo choose 1 more pair"
                if self.extraBots > 1:
                    extraBotText = f"\nGo choose {self.extraBots} more pairs"
                res = requests.post(
                    config.discordHook,
                    {
                        "content": f"""Bot `{self.bot['name']}` settings updated{extraBotText}"""
                    },
                )
            else:
                res = requests.post(
                    config.discordHook,
                    {
                        "content": f"""Bot `{self.bot['name']}` updated for `{divideInto}` pairs\n```{content}```"""
                    },
                )
        else:
            if self.auto:
                res = requests.post(config.discordHook, {"content": resp})
            else:
                print(resp)


if __name__ == "__main__":
    Main()
