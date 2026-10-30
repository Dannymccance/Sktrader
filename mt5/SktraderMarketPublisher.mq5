// Read-only XAUUSD quote and completed M1 candle publisher.
// Attach to a separate XAUUSD chart. NO order placement, no trade operations.
// Atomic CSV handoff to a VPS-side read-only ingestor via Terminal Common Files.
#property strict
#property version "1.00"
input string QuotesFile = "sktrader_xau_quotes.csv";
input string BarsFile = "sktrader_xau_m1.csv";
input string IdentityFile = "sktrader_xau_identity.csv";
input int PublishMs = 250;
input int BackfillBars = 1500;
input int BackfillTicks = 1000;
input bool RequireDemoAccount = true;

ulong last_tick_ms = 0;
datetime last_bar_time = 0;
bool configured = false;

bool SafeSymbol() {
   return StringFind(_Symbol,"XAUUSD") == 0;
}
bool ValidProfile() {
   if(!RequireDemoAccount) return true;
   return AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_DEMO;
}
int OnInit() {
   if(!SafeSymbol() || !ValidProfile()) {
      Print("Market publisher refused: requires XAUUSD chart and DEMO account");
      return INIT_FAILED;
   }
   if(PublishMs < 100 || BackfillBars < 2 || BackfillBars > 100000 ||
      BackfillTicks < 1 || BackfillTicks > 10000) return INIT_PARAMETERS_INCORRECT;
   EventSetMillisecondTimer(PublishMs);
   configured = true;
   Print("Read-only market data publisher started: ",_Symbol);
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason) { EventKillTimer(); }

void OnTimer() {
   if(!configured || !SafeSymbol() || !ValidProfile()) return;
   const string symbol = _Symbol;
   MqlTick latest;
   if(!SymbolInfoTick(symbol,latest)) return;
   if(latest.time_msc <= 0 || latest.bid <= 0 || latest.ask < latest.bid) return;

   // Re-export a bounded sliding tick window so receiver can recover from
   // interruptions. Receiver deduplicates by time_msc+bid+ask+flags.
   MqlTick ticks[];
   int tick_count = CopyTicks(symbol,ticks,COPY_TICKS_ALL,0,BackfillTicks);
   if(tick_count > 0 && (ulong)latest.time_msc != last_tick_ms) {
      string tmp = QuotesFile + ".tmp";
      int h = FileOpen(tmp,FILE_WRITE|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
      if(h != INVALID_HANDLE) {
         FileWrite(h,"time_msc","bid","ask","flags");
         for(int i=0;i<tick_count;i++) {
            if(ticks[i].bid <= 0 || ticks[i].ask < ticks[i].bid) continue;
            FileWrite(h,(string)ticks[i].time_msc,
                      DoubleToString(ticks[i].bid,_Digits),
                      DoubleToString(ticks[i].ask,_Digits),
                      (string)ticks[i].flags);
         }
         FileClose(h);
         if(FileMove(tmp,FILE_COMMON,QuotesFile,FILE_COMMON|FILE_REWRITE))
            last_tick_ms = (ulong)latest.time_msc;
      }
   }

   // Export ONLY closed M1 bars. The active bar (index zero) is excluded.
   datetime current_open = iTime(symbol,PERIOD_M1,0);
   if(current_open > 0 && current_open != last_bar_time) {
      MqlRates bars[];
      ArraySetAsSeries(bars,false);
      int bars_count = CopyRates(symbol,PERIOD_M1,1,BackfillBars,bars);
      if(bars_count > 0) {
         string tmp = BarsFile + ".tmp";
         int h = FileOpen(tmp,FILE_WRITE|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
         if(h != INVALID_HANDLE) {
            FileWrite(h,"time","open","high","low","close","tick_volume","spread");
            for(int i=0;i<bars_count;i++)
               FileWrite(h,(string)(long)bars[i].time,
                         DoubleToString(bars[i].open,_Digits),
                         DoubleToString(bars[i].high,_Digits),
                         DoubleToString(bars[i].low,_Digits),
                         DoubleToString(bars[i].close,_Digits),
                         (string)bars[i].tick_volume,(string)bars[i].spread);
            FileClose(h);
            if(FileMove(tmp,FILE_COMMON,BarsFile,FILE_COMMON|FILE_REWRITE))
               last_bar_time = current_open;
         }
      }
   }

   int f = FileOpen(IdentityFile+".tmp",FILE_WRITE|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
   if(f != INVALID_HANDLE) {
      FileWrite(f,"login","mode","currency","server","symbol",
                "contract_size","tick_size","tick_value","volume_min",
                "volume_max","volume_step","generated_at");
      FileWrite(f,(string)AccountInfoInteger(ACCOUNT_LOGIN),
                AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO?"DEMO":"REAL",
                AccountInfoString(ACCOUNT_CURRENCY),AccountInfoString(ACCOUNT_SERVER),symbol,
                DoubleToString(SymbolInfoDouble(symbol,SYMBOL_TRADE_CONTRACT_SIZE),4),
                DoubleToString(SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_SIZE),8),
                DoubleToString(SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE),8),
                DoubleToString(SymbolInfoDouble(symbol,SYMBOL_VOLUME_MIN),4),
                DoubleToString(SymbolInfoDouble(symbol,SYMBOL_VOLUME_MAX),4),
                DoubleToString(SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP),4),
                (string)(long)TimeGMT());
      FileClose(f);
      FileMove(IdentityFile+".tmp",FILE_COMMON,IdentityFile,FILE_COMMON|FILE_REWRITE);
   }
}
