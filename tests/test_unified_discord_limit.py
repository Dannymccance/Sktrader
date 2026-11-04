"""Unified Discord limit EA: one chart EA handles orders and broker telemetry."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EA = ROOT / "mt5" / "DiscordLimitUnified.mq5"
COMPILER = ROOT / "deploy" / "compile-discord-limit-unified.sh"


class UnifiedLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = EA.read_text()

    def test_account_agnostic_trading_and_telemetry(self):
        s = self.src
        self.assertNotIn("ExpectedAccountLogin", s)
        self.assertNotIn("ExpectedAccountServer", s)
        self.assertNotIn("RequiredTradeMode", s)
        self.assertNotIn("ApprovedAccount()", s)
        self.assertIn("input bool EnableNewEntries = false;", s)
        self.assertIn("string AccountDedupeScope()", s)
        self.assertIn("AccountInfoInteger(ACCOUNT_LOGIN)", s)
        self.assertIn("AccountInfoString(ACCOUNT_SERVER)", s)
        self.assertIn("ACCOUNT_TRADE_MODE_CONTEST", s)
        self.assertIn("ACCOUNT_TRADE_MODE_DEMO", s)
        self.assertIn("ACCOUNT_TRADE_MODE_REAL", s)
        self.assertIn("TradeModeLabel()", s)
        self.assertIn('StringFind(_Symbol,"XAUUSD")!=0', s)
        # Netting still attaches for telemetry; entries are prevented to
        # preserve independent 4%-risk positions and broker-native SL/TP.
        self.assertIn("bool SupportsIndependentPositions()", s)
        self.assertIn("if(!SupportsIndependentPositions()) {", s)
        self.assertIn("netting/exchange account", s)
        init = s[s.index("int OnInit()"):s.index("void OnDeinit(")]
        self.assertNotIn("INIT_FAILED", init.split('if(StringFind(_Symbol,',1)[0])
        self.assertNotIn("if(!SupportsIndependentPositions()) return INIT_FAILED", init)
        self.assertNotIn("ACCOUNT_MARGIN_MODE_RETAIL_HEDGING", init)
        self.assertNotIn("ACCOUNT_TRADE_MODE_REAL", init)

    def test_exact_trade_policy(self):
        s = self.src
        for pattern in (
            "trade.BuyLimit(", "trade.SellLimit(",
            "entry+(buy?-7.50:7.50)", "entry+(buy?15.0:-15.0)",
            "MathAbs(MathAbs(entry-sl)-7.50)>1e-6",
            "MathAbs(MathAbs(tp-entry)-15.0)>1e-6",
            "balance*0.04", "MathFloor(requested/step",
            "expires-issued!=1800", "ORDER_TIME_SPECIFIED",
            "if(AlreadyOrdered(id)) return MarkHandled(id);",
        ):
            self.assertIn(pattern, s)
        for pattern in (
            "trade.Buy(", "trade.Sell(", "trade.BuyStop(", "trade.SellStop(",
            "HasOpenAuroux", "MaxAggregateRisk", "MaxPortfolioRisk",
        ):
            self.assertNotIn(pattern, s)

    def test_one_ea_for_trading_and_telemetry(self):
        s=self.src
        self.assertEqual(s.count("void OnTimer()"), 1)
        self.assertEqual(s.count("int OnInit()"), 1)
        self.assertIn("ReadAndProcess(!EnableNewEntries);",s)
        self.assertIn("void PublishBrokerTelemetry()",s)
        self.assertIn("void PublishMarketTelemetry()",s)
        self.assertIn("void OnTradeTransaction(",s)
        self.assertIn('SnapshotFile = "discord_limit_account.csv"',s)
        self.assertIn('PositionFile = "discord_limit_positions.csv"',s)
        self.assertIn('DealFile = "discord_limit_deals.csv"',s)
        self.assertIn("input int BrokerTelemetrySeconds = 5;",s)
        self.assertIn("input int DealHistoryDays = 7;",s)
        self.assertIn("DealHistoryDays<1 || DealHistoryDays>30",s)
        self.assertIn("input bool ExportMarketTelemetry = false;",s)
        self.assertLess(s.index("ReadAndProcess(!EnableNewEntries);"),
                        s.index("PublishBrokerTelemetry();"))
        self.assertLess(s.index("ReadAndProcess(!EnableNewEntries);"),
                        s.index("PublishMarketTelemetry();"))

    def test_disable_new_entries_checkpoints_unhandled_signals(self):
        s=self.src
        self.assertIn("ReadAndProcess(!EnableNewEntries);",s)
        self.assertIn("bool firstStart=!FileIsExist(handledPath,FILE_COMMON)",s)
        self.assertIn('handledPath="DCL_"+AccountDedupeScope()',s)

    def test_account_probe_is_read_only(self):
        probe=ROOT / "deploy" / "inspect-mt5-accounts-readonly.sh"
        subprocess.run(["bash","-n",str(probe)],check=True,capture_output=True)
        source=probe.read_text()
        self.assertIn("MODE:",source)
        self.assertIn("SERVER:",source)
        self.assertIn("AGE_SECONDS:",source)
        for prohibited in ("systemctl restart","systemctl stop",
                           "docker restart","OrderSend","trade.Buy"):
            self.assertNotIn(prohibited,source)

    def test_compiler_only_and_no_gws_change(self):
        subprocess.run(["bash","-n",str(COMPILER)], check=True,
                       capture_output=True)
        content=COMPILER.read_text()
        self.assertIn("dkcapital-mt5-compiler-vnc",content)
        self.assertIn("DiscordLimitUnified.mq5",content)
        self.assertIn("No MT5 terminal was changed",content)
        self.assertIn("ACTUAL METAEDITOR COMPILER ERRORS",content)
        self.assertIn('"$stage/DiscordLimitUnified.log"',content)
        self.assertNotIn("systemctl restart",content)
        self.assertNotIn("systemctl stop",content)
        self.assertNotIn('install -o dkmt5c',content)


if __name__ == "__main__":
    unittest.main()
