import json


class Config:
    def __init__(self) -> None:
        try:
            with open("./config.json", "r") as openfile:
                config = json.load(openfile)
        except FileNotFoundError:
            config = {}
        self.binance_key: str = config["binance_key"]
        self.binance_secret: str = config["binance_secret"]
        self.telegram_token: str = config["telegram_token"]
        self.telegram_client_id: str = config["telegram_client_id"]
        self.tc_key: str = config["3commas_key"]
        self.tc_secret: str = config["3commas_secret"]
        self.simstartdate: str = config["simstartdate"]
        self.simenddate: str = config["simenddate"]
        self.granularity: str = config["granularity"]
