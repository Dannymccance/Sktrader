// One-channel XAUUSD fixed-distance limit follower. DEMO only.
// No market orders, trailing stops, break even, targets beyond TP1 or GWS access.
#property strict
#property version "1.00"
#include <Trade/Trade.mqh>
CTrade trade;

input string SignalFile = "discord_limit_30m_signals.csv"; // Terminal Common/Files
input string HandledFile = "discord_limit_30m_handled.txt"; // Durable per-message dedupe
input long ExpectedDemoLogin = 5058169035;
input ulong MagicNumber = 26110430; // Distinct from AUROUX and GWS
input int PollMilliseconds = 50;

string handled[];
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
   int h=FileOpen(HandledFile,FILE_READ|FILE_TXT|FILE_COMMON|FILE_ANSI);
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
   int f=FileOpen(HandledFile,FILE_READ|FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
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
bool Attempt(string id,string side,double entry,long issued,long expires) {
   if(IsHandled(id)) return true;
   long now=(long)TimeGMT();
   if(now>=expires || expires-issued!=1800 || issued>now+5) {
      Print("LIMIT expired or invalid timing ",id);
      return MarkHandled(id);
   }
   if(side!="BUY" && side!="SELL" || entry<=0) {
      Print("LIMIT malformed signal ",id);
      return MarkHandled(id);
   }
   bool buy=(side=="BUY");
   double sl=NormalizeDouble(entry+(buy?-10.0:10.0),_Digits);
   double tp=NormalizeDouble(entry+(buy?10.0:-10.0),_Digits);
   entry=NormalizeDouble(entry,_Digits);
   if(MathAbs(MathAbs(entry-sl)-10.0)>1e-6 ||
      MathAbs(MathAbs(tp-entry)-10.0)>1e-6 ||
      !OnTickGrid(entry) || !OnTickGrid(sl) || !OnTickGrid(tp)) {
      Print("LIMIT POI/SL/TP cannot be represented exactly on broker price grid ",id);
      return MarkHandled(id);
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
   if(AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_DEMO ||
      AccountInfoInteger(ACCOUNT_LOGIN)!=ExpectedDemoLogin) {
      Print("LIMIT refused non-demo or unexpected trading account");
      return INIT_FAILED;
   }
   if(_Symbol!="XAUUSD" && _Symbol!="XAUUSD+") {
      Print("LIMIT requires broker XAUUSD chart");
      return INIT_FAILED;
   }
   if(PollMilliseconds<50 || MagicNumber==0) return INIT_PARAMETERS_INCORRECT;
   bool firstStart=!FileIsExist(HandledFile,FILE_COMMON);
   LoadHandled();
   if(firstStart) {
      // Do not execute old Discord messages during initial attachment.
      int h=FileOpen(HandledFile,FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
      if(h==INVALID_HANDLE) return INIT_FAILED;
      FileClose(h);
      ReadAndProcess(true);
   }
   trade.SetExpertMagicNumber(MagicNumber);
   EventSetMillisecondTimer(PollMilliseconds);
   Print("LIMIT follower active: channel 1565527578163552327, 4% balance risk, $10 SL/TP, 30m expiry");
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason) { EventKillTimer(); }
void OnTimer() { ReadAndProcess(); }
