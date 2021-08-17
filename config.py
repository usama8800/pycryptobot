import json

import urllib3

urllib3.disable_warnings()


class Config:
    def __init__(self) -> None:
        try:
            with open("./config.json", "r") as openfile:
                config = json.load(openfile)
        except FileNotFoundError:
            config = {}
        self.binance_key: str = config["binance_key"]
        self.binance_secret: str = config["binance_secret"]
        self.tc_key: str = config["3commas_key"]
        self.tc_secret: str = config["3commas_secret"]
        self.simstartdate: str = config["simstartdate"]
        self.simenddate: str = config["simenddate"]
        self.granularity: str = config["granularity"]
        self.discordHook: str = config["discordHook"]
