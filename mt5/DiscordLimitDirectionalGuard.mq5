// Independent Sniper Pro directional-circuit-breaker EA, forked from DiscordLimitUnified.
// This file deliberately does not modify the original EA or Discord signal bridge.
// Guard: after an EA-owned SL within 2h, block matching direction ONLY when
// the price has moved >= $5 adversely since its 2h-ago M1 reference.
// Requires a fresh reference bar, current quote and broker deal history.
// No auto-cancellation of existing pending orders, no trade close, no new risk cap.
// Portable across logins, broker servers and DEMO/CONTEST/REAL account modes.
// Independent hedged XAUUSD exits require a hedging account; netting is telemetry-only.
// No market orders, trailing stops, break even, targets beyond TP1 or GWS access.
// Unrestricted overlap: each distinct eligible Discord signal can open a new order;
// only the SAME message ID is deduplicated. No aggregate portfolio risk cap.
#property strict
#property version "2.10"
#include <Trade/Trade.mqh>
CTrade trade;

input string SignalFile = "discord_limit_30m_signals.csv"; // Terminal Common/Files
input string HandledFile = "discord_limit_guard_handled.txt"; // Own per-message dedupe
input bool EnableNewEntries = false; // Explicit trading arm, OFF on every new attachment
input ulong MagicNumber = 26111520; // Separate from original DiscordLimitUnified
input int PollMilliseconds = 50;
input int BrokerTelemetrySeconds = 5; // Account, positions, deals
input int DealHistoryDays = 7; // Broker deal history export range
input bool ExportMarketTelemetry = false; // OFF for lowest-latency order handling
input int MarketTelemetryMs = 1000;
input int BackfillBars = 1500;
input int BackfillTicks = 1000;
input bool EnableDirectionalGuard = true; // New gate only; preserves the 4% per-trade rule
input bool GuardLogOnly = false; // If true, log would-block decisions but execute normally
input int GuardCooldownSeconds = 7200; // 2 hours after EA-owned same-direction SL
input int GuardTrendSeconds = 7200; // Compare current BID to about 2 hours ago
input double GuardAdverseUSD = 5.0; // Gold dollars, not pips
input int GuardMaxReferenceAgeSeconds = 90; // Avoid weekend/session gaps
input int GuardMaxQuoteAgeSeconds = 60; // Refuse trading with stale quotes during guard
input string GuardAuditFile = "discord_limit_guard_decisions.csv"; // Common/Files
input string SnapshotFile = "discord_guard_account.csv";
input string PositionFile = "discord_guard_positions.csv";
input string DealFile = "discord_guard_deals.csv";
input string QuotesFile = "discord_guard_xau_quotes.csv";
input string BarsFile = "discord_guard_xau_m1.csv";
input string IdentityFile = "discord_guard_xau_identity.csv";

string handled[];
string handledPath;
ulong lastBrokerTelemetryMs = 0;
ulong lastMarketTelemetryMs = 0;
bool telemetryUrgent = true;
ulong last_tick_ms = 0;
datetime last_bar_time = 0;

// Dedupe is isolated per connected login AND broker server (no fixed account whitelist).
string AccountDedupeScope() {
   string server=AccountInfoString(ACCOUNT_SERVER);
   long hash=0;
   for(int i=0;i<StringLen(server);i++)
      hash=(hash*131+(long)StringGetCharacter(server,i))%2147483647;
   return IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))+"_"+IntegerToString(hash);
}
bool SupportsIndependentPositions() {
   return AccountInfoInteger(ACCOUNT_MARGIN_MODE)==ACCOUNT_MARGIN_MODE_RETAIL_HEDGING;
}
string TradeModeLabel() {
   long mode=AccountInfoInteger(ACCOUNT_TRADE_MODE);
   if(mode==ACCOUNT_TRADE_MODE_DEMO) return "DEMO";
   if(mode==ACCOUNT_TRADE_MODE_CONTEST) return "CONTEST";
   if(mode==ACCOUNT_TRADE_MODE_REAL) return "REAL";
   return "UNKNOWN";
}
string firstFailureId = "";
datetime lastWarning = 0;
string lastSendId = "";
ulong lastSendAttempt = 0;

bool DigitsOnly(string s) {
   int n=StringLen(s);
   if(n<15 || n>22) return false;
   for(int i=0; i<n; i++) {
      ushort c=StringGetCharacter(s,i);
      if(c<'0' || c>'9') return false;
   }
   return true;
}
bool IsHandled(string id) {
   for(int i=ArraySize(handled)-1; i>=0; i--)
      if(handled[i]==id) return true;
   return false;
}
void LoadHandled() {
   int h=FileOpen(handledPath,FILE_READ|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(h==INVALID_HANDLE) return;
   while(!FileIsEnding(h)) {
      string id=FileReadString(h);
      StringTrimLeft(id); StringTrimRight(id);
      if(DigitsOnly(id) && !IsHandled(id)) {
         int at=ArraySize(handled);
         ArrayResize(handled,at+1);
         handled[at]=id;
      }
   }
   FileClose(h);
}
bool MarkHandled(string id) {
   if(IsHandled(id)) return true;
   int f=FileOpen(handledPath,FILE_READ|FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(f==INVALID_HANDLE) { Print("LIMIT dedupe file unavailable ",GetLastError());return false; }
   FileSeek(f,0,SEEK_END);
   bool wrote=FileWriteString(f,id+"\r\n")>0;
   FileFlush(f); FileClose(f);
   if(!wrote) return false;
   int at=ArraySize(handled);
   ArrayResize(handled,at+1);
   handled[at]=id;
   return true;
}
string OrderNote(string id) { return "DCL#" + id; }
bool AlreadyOrdered(string id) {
   string comment=OrderNote(id);
   for(int i=0;i<OrdersTotal();i++) {
      ulong t=OrderGetTicket(i);
      if(t>0 && OrderGetString(ORDER_COMMENT)==comment &&
         OrderGetInteger(ORDER_MAGIC)==(long)MagicNumber) return true;
   }
   for(int i=0;i<PositionsTotal();i++) {
      ulong t=PositionGetTicket(i);
      if(t>0 && PositionGetString(POSITION_COMMENT)==comment &&
         PositionGetInteger(POSITION_MAGIC)==(long)MagicNumber) return true;
   }
   if(HistorySelect(0,TimeCurrent())) {
      for(int i=HistoryOrdersTotal()-1;i>=0;i--) {
         ulong t=HistoryOrderGetTicket(i);
         if(t>0 && HistoryOrderGetString(t,ORDER_COMMENT)==comment &&
            HistoryOrderGetInteger(t,ORDER_MAGIC)==(long)MagicNumber) return true;
      }
   }
   return false;
}
void WarnOnce(string id,string reason) {
   if(firstFailureId!=id || TimeGMT()-lastWarning>=20) {
      Print("LIMIT waiting ",id,": ",reason);
      firstFailureId=id;
      lastWarning=TimeGMT();
   }
}
bool OnTickGrid(double price) {
   double tickSize=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   if(tickSize<=0) return false;
   double units=price/tickSize;
   return MathAbs(units-MathRound(units))<1e-6;
}
double BudgetVolume(bool buy,double entry,double sl) {
   double loss=0;
   ENUM_ORDER_TYPE side=buy?ORDER_TYPE_BUY:ORDER_TYPE_SELL;
   if(!OrderCalcProfit(side,_Symbol,1.0,entry,sl,loss) || loss>=0) return 0;
   double balance=AccountInfoDouble(ACCOUNT_BALANCE);
   double budget=balance*0.04;
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   double minimum=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double maximum=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   if(budget<=0 || step<=0 || minimum<=0 || maximum<=0) return 0;
   double requested=budget/(-loss);
   double volume=NormalizeDouble(MathFloor(requested/step+1e-9)*step,8);
   if(volume<minimum || volume>maximum || volume*(-loss)>budget+0.000001) return 0;
   return volume;
}

// Directional circuit breaker: act on an actual broker SL belonging to this EA.
// Broker server time is used for HistorySelect/DEAL_TIME and the M1 series.
// Matching by the opening deal/position ID also handles brokers that report
// DEAL_MAGIC=0 on the stop-triggered closing deal.
enum ENUM_GUARD_CHECK { GUARD_PASS=0, GUARD_BLOCK=1, GUARD_RETRY=2 };
string guardLoggedIds[];

bool GuardAlreadyLogged(const string id) {
   for(int i=ArraySize(guardLoggedIds)-1;i>=0;i--)
      if(guardLoggedIds[i]==id) return true;
   return false;
}
void GuardAudit(const string id,const bool buy,const datetime stopAt,
                const double reference,const double current,const double movement) {
   if(GuardAlreadyLogged(id)) return;
   int count=ArraySize(guardLoggedIds);
   ArrayResize(guardLoggedIds,count+1);
   guardLoggedIds[count]=id;
   int f=FileOpen(GuardAuditFile,FILE_READ|FILE_WRITE|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
   if(f==INVALID_HANDLE) {
      Print("GUARD AUDIT write failed ",GetLastError()," id=",id);
      return;
   }
   FileSeek(f,0,SEEK_END);
   FileWrite(f,id,(string)(long)TimeGMT(),buy?"BUY":"SELL",
             (string)(long)stopAt,DoubleToString(reference,_Digits),
             DoubleToString(current,_Digits),DoubleToString(movement,2),
             GuardLogOnly?"WOULD_BLOCK":"BLOCKED");
   FileFlush(f);
   FileClose(f);
}
bool WasOpenedByThisEA(const ulong positionId) {
   if(positionId==0 || !HistorySelectByPosition(positionId)) return false;
   for(int i=0;i<HistoryDealsTotal();i++) {
      ulong ticket=HistoryDealGetTicket(i);
      if(ticket==0) continue;
      if(HistoryDealGetString(ticket,DEAL_SYMBOL)!=_Symbol) continue;
      long entry=HistoryDealGetInteger(ticket,DEAL_ENTRY);
      if(entry!=DEAL_ENTRY_IN && entry!=DEAL_ENTRY_INOUT) continue;
      if(HistoryDealGetInteger(ticket,DEAL_MAGIC)==(long)MagicNumber) return true;
   }
   return false;
}
// Returns false on history failure; stopAt=0 means no matching SL.
bool GuardRecentStop(const bool buy,const datetime now,datetime &stopAt) {
   stopAt=0;
   if(!HistorySelect(now-GuardCooldownSeconds,now+60)) return false;

   // Save candidates BEFORE HistorySelectByPosition changes history selection.
   ulong positions[];
   datetime times[];
   int count=0;
   for(int i=0;i<HistoryDealsTotal();i++) {
      ulong deal=HistoryDealGetTicket(i);
      if(deal==0 || HistoryDealGetString(deal,DEAL_SYMBOL)!=_Symbol) continue;
      if(HistoryDealGetInteger(deal,DEAL_REASON)!=DEAL_REASON_SL) continue;
      long entry=HistoryDealGetInteger(deal,DEAL_ENTRY);
      if(entry!=DEAL_ENTRY_OUT && entry!=DEAL_ENTRY_OUT_BY) continue;
      long dealType=HistoryDealGetInteger(deal,DEAL_TYPE);
      if(buy && dealType!=DEAL_TYPE_SELL) continue; // SL closes a BUY with SELL
      if(!buy && dealType!=DEAL_TYPE_BUY) continue; // SL closes a SELL with BUY
      datetime when=(datetime)HistoryDealGetInteger(deal,DEAL_TIME);
      if(when<now-GuardCooldownSeconds || when>now) continue;
      if(HistoryDealGetInteger(deal,DEAL_MAGIC)==(long)MagicNumber) {
         if(when>stopAt) stopAt=when;
         continue;
      }
      ulong positionId=(ulong)HistoryDealGetInteger(deal,DEAL_POSITION_ID);
      if(positionId==0) continue;
      ArrayResize(positions,count+1);
      ArrayResize(times,count+1);
      positions[count]=positionId;
      times[count]=when;
      count++;
   }
   for(int j=0;j<count;j++) {
      if(times[j]>stopAt && WasOpenedByThisEA(positions[j]))
         stopAt=times[j];
   }
   return true;
}
ENUM_GUARD_CHECK DirectionalGuard(const string id,const bool buy) {
   if(!EnableDirectionalGuard) return GUARD_PASS;

   datetime now=TimeTradeServer();
   if(now<=0) { WarnOnce(id,"directional guard: server clock unavailable"); return GUARD_RETRY; }
   datetime stopped=0;
   if(!GuardRecentStop(buy,now,stopped)) {
      WarnOnce(id,"directional guard: deal history unavailable; retrying safely");
      return GUARD_RETRY;
   }
   if(stopped==0) return GUARD_PASS; // First contrarian signal is still permitted.

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.bid<=0 || tick.time<=0 ||
      tick.time>now+5 || now-tick.time>GuardMaxQuoteAgeSeconds) {
      WarnOnce(id,"directional guard: live quote stale; retrying safely");
      return GUARD_RETRY;
   }
   datetime target=now-GuardTrendSeconds;
   int shift=iBarShift(_Symbol,PERIOD_M1,target,false);
   if(shift<0) {
      WarnOnce(id,"directional guard: M1 history unavailable; retrying safely");
      return GUARD_RETRY;
   }
   datetime barAt=iTime(_Symbol,PERIOD_M1,shift);
   double reference=iOpen(_Symbol,PERIOD_M1,shift);
   // Never compare with Friday's candle as if it were Sunday's two-hour price.
   if(barAt<=0 || barAt>target ||
      target-barAt>GuardMaxReferenceAgeSeconds || reference<=0) {
      WarnOnce(id,"directional guard: two-hour reference stale; retrying safely");
      return GUARD_RETRY;
   }
   double delta=tick.bid-reference;
   bool adverse=buy ? (delta<=-GuardAdverseUSD) : (delta>=GuardAdverseUSD);
   if(!adverse) return GUARD_PASS;

   GuardAudit(id,buy,stopped,reference,tick.bid,delta);
   Print("DIRECTION_GUARD ",GuardLogOnly?"WOULD_BLOCK":"BLOCKED",
         " id=",id," side=",buy?"BUY":"SELL",
         " last_SL_server=",TimeToString(stopped,TIME_DATE|TIME_SECONDS),
         " prior_BID_M1open=",DoubleToString(reference,_Digits),
         " current_BID=",DoubleToString(tick.bid,_Digits),
         " 2h_delta=",DoubleToString(delta,2),
         " max_adverse=",DoubleToString(GuardAdverseUSD,2));
   return GuardLogOnly ? GUARD_PASS : GUARD_BLOCK;
}

bool Attempt(string id,string side,double entry,long issued,long expires) {
   if(IsHandled(id)) return true;
   long now=(long)TimeGMT();
   if(now>=expires || expires-issued!=1800 || issued>now+5) {
      Print("LIMIT expired or invalid timing ",id);
      return MarkHandled(id);
   }
   if((side!="BUY" && side!="SELL") || entry<=0) {
      Print("LIMIT malformed signal ",id);
      return MarkHandled(id);
   }
   bool buy=(side=="BUY");
   double requested=entry;
   double sl=NormalizeDouble(entry+(buy?-7.50:7.50),_Digits);
   double tp=NormalizeDouble(entry+(buy?15.0:-15.0),_Digits);
   entry=NormalizeDouble(entry,_Digits);
   if(MathAbs(requested-entry)>1e-7 ||
      MathAbs(MathAbs(entry-sl)-7.50)>1e-6 ||
      MathAbs(MathAbs(tp-entry)-15.0)>1e-6 ||
      !OnTickGrid(entry) || !OnTickGrid(sl) || !OnTickGrid(tp)) {
      Print("LIMIT POI/SL/TP cannot be represented exactly on broker price grid ",id);
      return MarkHandled(id);
   }
   if(!EnableNewEntries) return false;
   if(!SupportsIndependentPositions()) {
      // A netting position has only ONE broker SL/TP for the entire XAUUSD
      // exposure. Submitting overlapping signals would silently break the
      // per-signal 4% risk and 2R exit rules. Allow read-only telemetry.
      WarnOnce(id,"netting/exchange account: independent overlapping SL/TP not supported");
      return false;
   }
   if(AccountInfoInteger(ACCOUNT_LOGIN)<=0 || !AccountInfoInteger(ACCOUNT_TRADE_ALLOWED)) {
      WarnOnce(id,"no connected trade-enabled MT5 account");
      return false;
   }
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ||
      !MQLInfoInteger(MQL_TRADE_ALLOWED) ||
      !AccountInfoInteger(ACCOUNT_TRADE_EXPERT)) {
      WarnOnce(id,"Algo Trading disabled");
      return false;
   }
   int modes=(int)SymbolInfoInteger(_Symbol,SYMBOL_EXPIRATION_MODE);
   if((modes & SYMBOL_EXPIRATION_SPECIFIED)==0) {
      WarnOnce(id,"broker does not support specified 30 minute expiration");
      return false;
   }
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.ask<=0 || tick.bid<=0) return false;
   double distance=(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*_Point;
   // Only limit orders. Never substitute a market order or stop order.
   if((buy && entry>=tick.ask-distance) || (!buy && entry<=tick.bid+distance)) {
      return false; // Recheck as quotes change until original 30m deadline.
   }
   double volume=BudgetVolume(buy,entry,sl);
   if(volume<=0) {
      WarnOnce(id,"4% risk cannot be represented in broker lot increments");
      return false;
   }
   if(AlreadyOrdered(id)) return MarkHandled(id);
   ENUM_GUARD_CHECK guard=DirectionalGuard(id,buy);
   if(guard==GUARD_BLOCK) return MarkHandled(id); // Do not revive filtered orders later.
   if(guard==GUARD_RETRY) return false; // Missing data is NOT an implicit pass.
   // Hard cooldown prevents broker request storms on transient failures.
   ulong ms=GetTickCount64();
   if(id==lastSendId && ms-lastSendAttempt<1000) return false;
   lastSendId=id;
   lastSendAttempt=ms;
   datetime brokerExpiry=(datetime)((long)TimeTradeServer()+expires-now);
   if(brokerExpiry<=TimeTradeServer() || TimeGMT()>=expires) return false;
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetTypeFillingBySymbol(_Symbol);
   bool sent = buy
      ? trade.BuyLimit(volume,entry,_Symbol,sl,tp,ORDER_TIME_SPECIFIED,brokerExpiry,OrderNote(id))
      : trade.SellLimit(volume,entry,_Symbol,sl,tp,ORDER_TIME_SPECIFIED,brokerExpiry,OrderNote(id));
   uint ret=trade.ResultRetcode();
   bool accepted=sent && (ret==TRADE_RETCODE_PLACED || ret==TRADE_RETCODE_DONE);
   if(accepted) {
      Print("LIMIT placed ",id," ",side," lots=",volume,
            " POI=",entry," SL=",sl," TP1=",tp," expires=",expires,
            " order=",trade.ResultOrder());
      return MarkHandled(id);
   }
   Print("LIMIT broker rejected ",id," code=",ret," ",trade.ResultRetcodeDescription());
   // Never claim an order was rejected definitively if broker state is ambiguous.
   // AlreadyOrdered() checks both live and historical orders on the next attempt.
   return false;
}
void ReadAndProcess(bool baselineOnly=false) {
   int f=FileOpen(SignalFile,FILE_READ|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
   if(f==INVALID_HANDLE) return;
   while(!FileIsEnding(f)) {
      string id=FileReadString(f);
      if(FileIsEnding(f)) break;
      string side=FileReadString(f);
      string poiText=FileReadString(f);
      long issued=(long)StringToInteger(FileReadString(f));
      long expires=(long)StringToInteger(FileReadString(f));
      if(!DigitsOnly(id) || (side!="BUY" && side!="SELL")) continue;
      if(baselineOnly) {
         if(!MarkHandled(id)) Print("LIMIT first-install checkpoint write failed");
         continue;
      }
      if(IsHandled(id)) continue;
      // Reject malformed prices instead of letting StringToDouble silently coerce.
      if(StringLen(poiText)==0 || StringToDouble(poiText)<=0) {
         MarkHandled(id); continue;
      }
      Attempt(id,side,StringToDouble(poiText),issued,expires);
   }
   FileClose(f);
}
int OnInit() {
   if(StringFind(_Symbol,"XAUUSD")!=0) {
      Print("LIMIT requires XAUUSD or a broker XAUUSD-suffixed chart");
      return INIT_FAILED;
   }
   if(PollMilliseconds<50 || MagicNumber==0 || BrokerTelemetrySeconds<1 ||
      DealHistoryDays<1 || DealHistoryDays>30 ||
      MarketTelemetryMs<100 || BackfillBars<2 || BackfillBars>100000 ||
      BackfillTicks<1 || BackfillTicks>10000 ||
      GuardCooldownSeconds<60 || GuardCooldownSeconds>86400 ||
      GuardTrendSeconds<60 || GuardTrendSeconds>86400 ||
      GuardAdverseUSD<=0 || GuardMaxReferenceAgeSeconds<60 ||
      GuardMaxReferenceAgeSeconds>300 || GuardMaxQuoteAgeSeconds<1 ||
      GuardMaxQuoteAgeSeconds>300 || GuardAuditFile=="")
      return INIT_PARAMETERS_INCORRECT;
   handledPath="DCL_"+AccountDedupeScope()+"_"+IntegerToString((long)MagicNumber)+"_"+HandledFile;
   bool firstStart=!FileIsExist(handledPath,FILE_COMMON);
   LoadHandled();
   if(firstStart) {
      // Do not execute old Discord messages during initial attachment.
      int h=FileOpen(handledPath,FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
      if(h==INVALID_HANDLE) return INIT_FAILED;
      FileClose(h);
      ReadAndProcess(true);
   }
   trade.SetExpertMagicNumber(MagicNumber);
   EventSetMillisecondTimer(PollMilliseconds);
   Print("UNIFIED LIMIT+TELEMETRY on ",AccountInfoString(ACCOUNT_SERVER),
         " login ",AccountInfoInteger(ACCOUNT_LOGIN),
         " mode=",TradeModeLabel(),
         " margin_mode=",AccountInfoInteger(ACCOUNT_MARGIN_MODE),
         " new_entries=",EnableNewEntries,
         " | XAUUSD limits: 4% balance, $7.50 SL, $15 TP, 30m.",
         " guard=",EnableDirectionalGuard,
         " log_only=",GuardLogOnly,
         " cooldown_sec=",GuardCooldownSeconds,
         " trend_sec=",GuardTrendSeconds,
         " adverse_USD=",GuardAdverseUSD);
   if(!SupportsIndependentPositions())
      Print("LIMIT warning: netting/exchange account: telemetry active, independent-position trading blocked");
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason) { EventKillTimer(); }
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result) {
   telemetryUrgent=true;
}
void OnTimer() {
   // Order placement ALWAYS executes first. Telemetry cannot preempt this step.
   // The EA remains attached for telemetry on ANY account mode.
   ReadAndProcess(!EnableNewEntries);
   ulong now=GetTickCount64();
   // Trade transaction bursts can produce multiple callbacks. Coalesce
   // urgent broker exports to avoid expensive repeated history scans.
   if((telemetryUrgent && (lastBrokerTelemetryMs==0 ||
       now-lastBrokerTelemetryMs>=500)) ||
      lastBrokerTelemetryMs==0 ||
      now-lastBrokerTelemetryMs>=(ulong)BrokerTelemetrySeconds*1000) {
      lastBrokerTelemetryMs=now;
      telemetryUrgent=false;
      PublishBrokerTelemetry();
   }
   if(ExportMarketTelemetry && (lastMarketTelemetryMs==0 ||
      now-lastMarketTelemetryMs>=(ulong)MarketTelemetryMs)) {
      lastMarketTelemetryMs=now;
      PublishMarketTelemetry();
   }
}

// Read-only broker telemetry exports, separate names from AUROUX and GWS.
bool AtomicFile(const string temporary,const string destination) {
   return FileMove(temporary,FILE_COMMON,destination,FILE_COMMON|FILE_REWRITE);
}
void PublishBrokerTelemetry() {
   string tmp=SnapshotFile+".tmp";
   int f=FileOpen(tmp,FILE_WRITE|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
   if(f==INVALID_HANDLE) { Print("account CSV open failed ",GetLastError());return; }
   FileWrite(f,"login","mode","currency","server","generated_at",
             "balance","equity","margin","margin_free","floating_profit",
             "positions","orders");
   FileWrite(f,(string)AccountInfoInteger(ACCOUNT_LOGIN),
             TradeModeLabel(),
             AccountInfoString(ACCOUNT_CURRENCY),AccountInfoString(ACCOUNT_SERVER),
             (string)(long)TimeGMT(),
             DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),2),
             DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),2),
             DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN),2),
             DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE),2),
             DoubleToString(AccountInfoDouble(ACCOUNT_PROFIT),2),
             (string)PositionsTotal(),(string)OrdersTotal());
   FileClose(f);
   if(!AtomicFile(tmp,SnapshotFile)) Print("account CSV move failed ",GetLastError());

   tmp=PositionFile+".tmp";
   f=FileOpen(tmp,FILE_WRITE|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
   if(f==INVALID_HANDLE) { Print("positions CSV open failed ",GetLastError());return; }
   FileWrite(f,"ticket","identifier","symbol","type","magic","volume",
             "price_open","sl","tp","price_current","profit","swap","time_msc");
   for(int i=0;i<PositionsTotal();i++) {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      FileWrite(f,(string)ticket,
                (string)PositionGetInteger(POSITION_IDENTIFIER),
                PositionGetString(POSITION_SYMBOL),
                (string)PositionGetInteger(POSITION_TYPE),
                (string)PositionGetInteger(POSITION_MAGIC),
                DoubleToString(PositionGetDouble(POSITION_VOLUME),4),
                DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN),8),
                DoubleToString(PositionGetDouble(POSITION_SL),8),
                DoubleToString(PositionGetDouble(POSITION_TP),8),
                DoubleToString(PositionGetDouble(POSITION_PRICE_CURRENT),8),
                DoubleToString(PositionGetDouble(POSITION_PROFIT),2),
                DoubleToString(PositionGetDouble(POSITION_SWAP),2),
                (string)PositionGetInteger(POSITION_TIME_MSC));
   }
   FileClose(f);
   if(!AtomicFile(tmp,PositionFile)) Print("positions CSV move failed ",GetLastError());

   datetime end_time=TimeCurrent();
   if(end_time<=0) end_time=TimeGMT();
   datetime start_time=end_time-(datetime)(DealHistoryDays*86400);
   if(!HistorySelect(start_time,end_time)) {
      Print("HistorySelect failed ",GetLastError());
      return;
   }
   tmp=DealFile+".tmp";
   f=FileOpen(tmp,FILE_WRITE|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
   if(f==INVALID_HANDLE) { Print("deals CSV open failed ",GetLastError());return; }
   FileWrite(f,"ticket","order","position_id","symbol","type","entry",
             "magic","time_msc","volume","price","profit","commission","swap","fee");
   for(int j=0;j<HistoryDealsTotal();j++) {
      ulong deal=HistoryDealGetTicket(j);
      if(deal==0) continue;
      FileWrite(f,(string)deal,
                (string)HistoryDealGetInteger(deal,DEAL_ORDER),
                (string)HistoryDealGetInteger(deal,DEAL_POSITION_ID),
                HistoryDealGetString(deal,DEAL_SYMBOL),
                (string)HistoryDealGetInteger(deal,DEAL_TYPE),
                (string)HistoryDealGetInteger(deal,DEAL_ENTRY),
                (string)HistoryDealGetInteger(deal,DEAL_MAGIC),
                (string)HistoryDealGetInteger(deal,DEAL_TIME_MSC),
                DoubleToString(HistoryDealGetDouble(deal,DEAL_VOLUME),4),
                DoubleToString(HistoryDealGetDouble(deal,DEAL_PRICE),8),
                DoubleToString(HistoryDealGetDouble(deal,DEAL_PROFIT),2),
                DoubleToString(HistoryDealGetDouble(deal,DEAL_COMMISSION),2),
                DoubleToString(HistoryDealGetDouble(deal,DEAL_SWAP),2),
                DoubleToString(HistoryDealGetDouble(deal,DEAL_FEE),2));
   }
   FileClose(f);
   if(!AtomicFile(tmp,DealFile)) Print("deals CSV move failed ",GetLastError());
}

// Optional quote/candle telemetry. Disabled by default for execution latency.
void PublishMarketTelemetry() {
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
                TradeModeLabel(),
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
