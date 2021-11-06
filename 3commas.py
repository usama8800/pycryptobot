import sys
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
from binance.client import Client
from py3cw.request import Py3CW
from tqdm import tqdm
from enum import Enum

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


def cmpFloat(a, b):
    return abs(a - b) < 0.01

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
    bo, so, mstc, os
):
    return (1 - os ** mstc) / (
        1 - os
    ) * so + bo


def getBounceFromSettings(
    so,
    mstc,
    safetyVolumeScale,
    safetyStep,
    takeProfit,
    safetyStepScale
):
    priceDrops = [0]
    for i in range(mstc):
        priceDrops.append(priceDrops[-1] + safetyStep * safetyStepScale ** i)
    prices = list(map(lambda x: 100 - x, priceDrops))
    if prices[-1] < 0:
        return float("inf"), float("inf")
    volumes = [
        10,
        *[so * safetyVolumeScale ** i for i in range(0, mstc)],
    ]
    avgPrice = np.dot(prices, volumes) / sum(volumes)
    tpPrice = avgPrice * (1 + takeProfit / 100)
    return priceDrops[-1], tpPrice - 100


def printSafetys(
    bo,
    so,
    mstc,
    os,
    sos,
    ss,
    takeProfit,
):
    originalPrice = 100
    currentPrice = originalPrice
    buyPrices = [currentPrice]
    avgBuyPrice = currentPrice
    needed = bo + so
    buyVolumes = [bo]
    boughtCoins = bo / currentPrice
    df = pd.DataFrame(
        {
            "Price": pd.Series([], dtype="float"),
            "Price Drop": pd.Series([], dtype="float"),
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
        0,
        bo,
        avgBuyPrice,
        tpPrice,
        (tpPrice - originalPrice) / originalPrice * 100,
        boughtCoins * tpPrice - bo,
    ]
    for i in range(mstc):
        if i != 0:
            so *= os
            needed += so
        currentPrice -= originalPrice * sos * ss ** i / 100
        buyPrices.append(currentPrice)
        buyVolumes.append(so)
        avgBuyPrice = np.dot(buyPrices, buyVolumes) / sum(buyVolumes)
        tpPrice = avgBuyPrice * (1 + takeProfit / 100)
        boughtCoins += so / currentPrice
        df.loc[i + 1] = [
            currentPrice,
            originalPrice-currentPrice,
            so,
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

def getBestBotSettings(usdt, givenBounce=0, minSafetys=7):
    # base order, safety order, max safetys, safety volume scale, used usdt, safety step, safety step scale, lowest tp %
    if minSafetys is None:
        minSafetys = 7
    settings = []
    for bo in range(10, 20):
        for so in (np.arange(bo, bo * 3, 0.1)):
            for safetyVolumeScale in np.arange(1.01, 2, 0.01):
                for safetyStep in np.arange(1.1, 3, 0.01):
                    for safetyStepScale in np.arange(1, 1.501, 0.01):
                        for mstc in range(minSafetys, 101):
                            neededUSDT = getNeededUSDTFromSettings(
                                bo,
                                so,
                                mstc,
                                safetyVolumeScale,
                            )
                            if neededUSDT > usdt:
                                break
                            _, bounce = getBounceFromSettings(
                                so,
                                mstc,
                                safetyVolumeScale,
                                safetyStep,
                                1.5,
                                safetyStepScale
                            )

                            # must haves
                            if bounce > -givenBounce:
                                continue
                            # priorities
                            settings.append(
                                (
                                    safetyVolumeScale,
                                    so,
                                    -bounce,
                                    neededUSDT,
                                    bo,
                                    mstc,
                                    safetyStepScale
                                    -safetyStep,
                                )
                            )
                        if mstc < 100:
                            break


    # base order, safety order, max safetys, safety volume scale, used usdt, safety step, safety step scale, lowest tp %
    settings.sort()
    settings = [(x[4], x[1], x[5], x[0], x[3], -x[7], x[6], -x[2]) for x in settings]
    # [print(setting) for setting in settings[-100:]]
    if len(settings):
        return settings[-1]
    return 0,0,0,0,0,0,0,0

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
            "id",
            str(Properties.BO),
            str(Properties.SO),
            str(Properties.OS),
            str(Properties.SS),
            str(Properties.SOS),
            str(Properties.TP),
            str(Properties.MSTC),
            "max_active_deals",
            "active_safety_orders_count",
        ]
    ] = bots[
        [
            "id",
            str(Properties.BO),
            str(Properties.SO),
            str(Properties.OS),
            str(Properties.SS),
            str(Properties.SOS),
            str(Properties.TP),
            str(Properties.MSTC),
            "max_active_deals",
            "active_safety_orders_count",
        ]
    ].apply(
        pd.to_numeric
    )
    return bots.loc[0]

def fullPrint(df, rows=None, cols=None):
    with pd.option_context("display.max_rows", rows, "display.max_columns", cols):
        print(df)

class Properties(Enum):
    BO = 'base_order_volume'
    SO = 'safety_order_volume'
    OS = 'martingale_volume_coefficient'
    SS = 'martingale_step_coefficient'
    SOS = 'safety_order_step_percentage'
    TP = 'take_profit'
    MSTC = 'max_safety_orders'

    def __str__(self) -> str:
        return self.value
    def __repr__(self) -> str:
        return self.value

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
        self.onlySafetyOrder = False

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
            elif key.startswith("o-") or key.startswith("only-"):
                key = key[key.index("-")+1:]
                if key == "so":
                    self.onlySafetyOrder = int(val) == 1
                else:
                    raise KeyError(f"Unknown property '{key}'")
            else:
                raise KeyError(f"Unknown option '{arg}'")

            if self.needed and self.safetys:
                raise KeyError(f"Options --safetys and --needed conflict")
            if self.needed and self.profits:
                raise KeyError(f"Options --profits and --needed conflict")
            if self.safetys and self.profits:
                raise KeyError(f"Options --profits and --safetys conflict")
        self.bot = get_bot()
        self.numBots = self.bot["max_active_deals"] + self.extraBots
        if not self.bounce and self.extraSafetys is None:
            self.bounce = int(-getBounceFromSettings(
                self.bot['safety_order_volume'],
                self.bot['max_safety_orders'],
                self.bot['martingale_volume_coefficient'],
                self.bot[str(Properties.SOS)],
                self.bot[str(Properties.TP)],
                self.bot[str(Properties.SS)],
            )[1]) + self.extraBounce
        self.main()

    def setTotalUSDT(self):
        if self.usdt is None:
            balances = getBalances()
            bnb = balances[balances["asset"] == "BNB"]["total"].values[0]
            if bnb < 10:
                res = requests.post(
                    config.discordHook,
                    {
                        "content": f"""{bnb} BNB left!!!!"""
                    },
                )
                
            self.usdt = balances[balances["asset"] == "USDT"]["total"].values[0]
            resp = p3cw.request(
                entity="deals",
                action="",
                payload={"bot_id": self.bot["id"], "scope": "active"},
            )
            deals = pd.DataFrame(resp[1])
            deals = deals[["pair", "bought_volume", str(Properties.TP)]]

            for i in range(len(deals)):
                deal = deals.loc[i]
                self.usdt += float(deal['bought_volume']) * (1 + float(deal['take_profit'])/100)

    def getBotSettingsChangeOnlyOneProperty(self, property):
        usdtPerBot = (self.usdt + self.extraUSDT) / self.numBots
        if property == Properties.SO:
            for val in (np.arange(self.bot[str(Properties.SO)], self.bot[str(Properties.BO)] * 3, 0.1)):
                neededUSDT = getNeededUSDTFromSettings(
                    self.bot[str(Properties.BO)],
                    val,
                    self.bot[str(Properties.MSTC)],
                    self.bot[str(Properties.OS)],
                )
                if neededUSDT > usdtPerBot:
                    break
        return val


    def main(self):
        if self.needed:
            needed = getNeededUSDTFromSettings(
                self.bot[str(Properties.BO)],
                self.bot[str(Properties.SO)],
                self.bot[str(Properties.MSTC)],
                self.bot[str(Properties.OS)],
            )
            print(f"{needed*self.self.numBots:.2f}")
            return

        if self.safetys:
            printSafetys(
                self.bot[str(Properties.BO)],
                self.bot[str(Properties.SO)],
                self.bot[str(Properties.MSTC)],
                self.bot[str(Properties.OS)],
                self.bot[str(Properties.SOS)],
                self.bot[str(Properties.SS)],
                self.bot[str(Properties.TP)],
            )
            return

        if self.profits:
            printProfits(self.bot["pairs"], self.days)
            return

        self.setTotalUSDT()
        minSafetys = None if self.extraSafetys is None else +self.bot[str(Properties.MSTC)] + self.extraSafetys

        (
            bo,
            so,
            mstc,
            os,
            neededUSDT,
            sos,
            ss,
            lowestTPPercent,
        ) = (
            self.bot[str(Properties.BO)],
            self.bot[str(Properties.SO)],
            self.bot[str(Properties.MSTC)],
            self.bot[str(Properties.OS)],
            getNeededUSDTFromSettings(self.bot[str(Properties.BO)], self.bot[str(Properties.SO)], self.bot[str(Properties.MSTC)], self.bot[str(Properties.OS)]),
            self.bot[str(Properties.SOS)],
            self.bot[str(Properties.SS)],
            getBounceFromSettings(self.bot[str(Properties.SO)], self.bot[str(Properties.MSTC)], self.bot[str(Properties.OS)], self.bot[str(Properties.SOS)], self.bot[str(Properties.TP)], self.bot[str(Properties.SS)])[1]
        )
        if self.onlySafetyOrder:
            so = self.getBotSettingsChangeOnlyOneProperty(Properties.SO)
        else:
            (
                bo,
                so,
                mstc,
                os,
                neededUSDT,
                sos,
                ss,
                lowestTPPercent,
            ) = getBestBotSettings((self.usdt + self.extraUSDT) / self.numBots, self.bounce, minSafetys)

        if bo == 0:
            extra = ''
            if self.extraBounce:
                extra = f" and {self.bounce} bounce"
            print(
                f"""Bot "{self.bot['name']}" settings for {self.numBots} pairs
with {self.usdt:.0f} USDT{extra}

Unfeasable"""
            )
            return

        if self.safetys is None:
            printSafetys(
                bo,
                so,
                mstc,
                os,
                sos,
                ss,
                self.bot[str(Properties.TP)],
            )
            print()

        content = f"""
Base Order:                {bo}
Safety Order Size:         {so:.2f}
Safety Order Volume Scale: {os:.2f}
Safety Order Step:         {sos:.2f}
Safety Order Step Scale:   {ss:.2f}
Max Safety Order:          {mstc}
Using $ / bot:             {neededUSDT:.2f}
Using $:                   {neededUSDT*self.numBots:.2f} / {self.usdt:.2f}
Lowest Take Profit %:      {lowestTPPercent:.2f}"""
        if self.auto:
            res = requests.post(
                config.discordHook,
                {
                    "content": f"""Bot `{self.bot['name']}` settings for `{self.numBots}` pairs\n```{content}```"""
                },
            )
        else:
            print(f"""Bot "{self.bot['name']}" settings for {self.numBots} pairs""")
            print(content)

        allSame = (
            cmpFloat(bo, self.bot[str(Properties.BO)])
            and cmpFloat(so, self.bot[str(Properties.SO)])
            and cmpFloat(
                os, self.bot[str(Properties.OS)]
            )
            and mstc == self.bot[str(Properties.MSTC)]
            and cmpFloat(
                sos, self.bot[str(Properties.SOS)]
            )
            and cmpFloat(ss, self.bot[str(Properties.SS)])
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
                "max_active_deals": self.numBots,
                Properties.BO: bo,
                Properties.TP: self.bot[str(Properties.TP)],
                Properties.SO: so,
                Properties.OS: os,
                Properties.SS: ss,
                Properties.MSTC: mstc,
                "active_safety_orders_count": self.bot["active_safety_orders_count"],
                Properties.SOS: sos,
                "take_profit_type": self.bot["take_profit_type"],
                "strategy_list": self.bot["strategy_list"],
                "bot_id": self.bot["id"],
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
                        "content": f"""Bot `{self.bot['name']}` updated for `{self.numBots}` pairs\n```{content}```"""
                    },
                )
        else:
            if self.auto:
                res = requests.post(config.discordHook, {"content": resp})
            else:
                print(resp)


if __name__ == "__main__":
    Main()
