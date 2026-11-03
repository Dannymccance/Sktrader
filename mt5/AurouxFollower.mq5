// AUROUX XAUUSD follower. Standalone from GWS. Compile in MetaEditor (MT5).
#property strict
#property version "1.11"
#include <Trade/Trade.mqh>
CTrade trade;
input string SignalFile = "auroux_signals.csv"; // MT5 Terminal Common/Files
input long ExpectedDemoLogin = 5058169035; // Safety: only the approved AUROUX demo account
input double RiskPercent = 4.0;
input bool RoundVolumeUp = false; // Never round above the risk budget
input ulong MagicNumber = 2601030;
input double MaxAdversePOIFraction = 0.10; // Reject >10% adverse move versus POI
input int WeeklyOpenSundayUTCMinute = 1320; // 22:00 UTC; set to actual weekly broker open, adjust for DST
input int MarketOpenBlockMinutes = 180;
input int CheckMilliseconds = 100;
input bool SkipHistoryOnFirstStart = true;
string checkpoint;

void WriteHealth() {
   static datetime lastWritten=0;
   datetime now=TimeGMT();
   if(now>0 && lastWritten>0 && now-lastWritten<5) return;
   lastWritten=now;
   int f=FileOpen("auroux_follower_health.csv",FILE_WRITE|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
   if(f==INVALID_HANDLE) return;
   bool tradingAllowed=(bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)
      && (bool)MQLInfoInteger(MQL_TRADE_ALLOWED)
      && (bool)AccountInfoInteger(ACCOUNT_TRADE_ALLOWED)
      && (bool)AccountInfoInteger(ACCOUNT_TRADE_EXPERT);
   long lastId=GlobalVariableCheck(checkpoint)?(long)GlobalVariableGet(checkpoint):0;
   FileWrite(f,"generated_at","login","mode","symbol","trade_allowed","checkpoint","build");
   FileWrite(f,(string)(long)now,
      (string)AccountInfoInteger(ACCOUNT_LOGIN),
      AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO?"DEMO":"REAL",
      _Symbol,tradingAllowed?"1":"0",(string)lastId,"1.11");
   FileClose(f);
}

bool InMarketOpenBlock() {
   if(WeeklyOpenSundayUTCMinute<0 || WeeklyOpenSundayUTCMinute>=1440 || MarketOpenBlockMinutes<0) return true;
   MqlDateTime t; TimeToStruct(TimeGMT(),t);
   int minuteOfWeek=t.day_of_week*1440+t.hour*60+t.min;
   int openMinute=WeeklyOpenSundayUTCMinute;
   int since=(minuteOfWeek-openMinute+10080)%10080;
   return since<MarketOpenBlockMinutes;
}

bool IsPending(string orderType) {
   return orderType=="BUY_LIMIT" || orderType=="SELL_LIMIT" || orderType=="BUY_STOP" || orderType=="SELL_STOP";
}


string SignalComment(long id) { return "AUR#" + IntegerToString(id); }

double RoundedVolume(double raw) {
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   double minimum=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double maximum=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   if(step<=0 || raw<=0) return 0;
   double units=RoundVolumeUp ? MathCeil(raw/step-1e-9) : MathFloor(raw/step+1e-9);
   double lot=NormalizeDouble(units*step,8);
   if(lot<minimum || lot>maximum) return 0;
   return lot;
}
bool HasOpenAuroux() {
   for(int i=0;i<PositionsTotal();i++) {
      ulong ticket=PositionGetTicket(i);
      if(ticket>0 && PositionGetInteger(POSITION_MAGIC)==(long)MagicNumber && PositionGetString(POSITION_SYMBOL)==_Symbol) return true;
   }
   for(int i=0;i<OrdersTotal();i++) {
      ulong ticket=OrderGetTicket(i);
      if(ticket>0 && OrderGetInteger(ORDER_MAGIC)==(long)MagicNumber && OrderGetString(ORDER_SYMBOL)==_Symbol) return true;
   }
   return false;
}
bool WasSubmitted(long id) {
   string comment=SignalComment(id);
   if(HistorySelect(0,TimeCurrent())) {
      for(int i=HistoryOrdersTotal()-1;i>=0;i--) {
         ulong ticket=HistoryOrderGetTicket(i);
         if(ticket>0 && HistoryOrderGetString(ticket,ORDER_COMMENT)==comment
            && HistoryOrderGetInteger(ticket,ORDER_MAGIC)==(long)MagicNumber) return true;
      }
   }
   for(int i=0;i<OrdersTotal();i++) {
      ulong ticket=OrderGetTicket(i);
      if(ticket>0 && OrderGetString(ORDER_COMMENT)==comment
         && OrderGetInteger(ORDER_MAGIC)==(long)MagicNumber) return true;
   }
   return false;
}
void CancelExpiredPending() {
   int handle=FileOpen(SignalFile,FILE_READ|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
   if(handle==INVALID_HANDLE) return;
   long now=(long)TimeGMT();
   while(!FileIsEnding(handle)) {
      long id=(long)StringToInteger(FileReadString(handle));
      if(FileIsEnding(handle)) break;
      FileReadString(handle); FileReadString(handle); FileReadString(handle); FileReadString(handle);
      long deadline=(long)StringToInteger(FileReadString(handle));
      FileReadString(handle); // explicit order type, or MARKET for old records
      if(id<=0 || deadline<=0 || now<deadline) continue;
      string note=SignalComment(id);
      for(int i=OrdersTotal()-1;i>=0;i--) {
         ulong ticket=OrderGetTicket(i);
         if(ticket==0 || OrderGetInteger(ORDER_MAGIC)!=(long)MagicNumber
            || OrderGetString(ORDER_COMMENT)!=note) continue;
         trade.SetExpertMagicNumber(MagicNumber);
         if(!trade.OrderDelete(ticket))
            Print("AUROUX expired pending cancellation failed ",id," ticket ",ticket,
                  " ",trade.ResultRetcodeDescription());
      }
   }
   FileClose(handle);
}
bool Execute(long id,string direction,double poi,double sl,double tp,long deadline,string orderType) {
   if(deadline<=0 || (long)TimeGMT()>=deadline) {
      Print("AUROUX expired signal ignored ",id);
      return true;
   }
   if(_Symbol!="XAUUSD" && _Symbol!="XAUUSD+") { Print("Attach to broker XAUUSD chart"); return false; }
   if(WasSubmitted(id)) return true;
   if(InMarketOpenBlock()) { Print("AUROUX weekly open exclusion ignored ",id); return true; }
   if(orderType=="" || orderType=="MARKET") orderType="MARKET";
   else if(!IsPending(orderType)) { Print("AUROUX unsupported order instruction ",id); return true; }
   // One AUROUX position or working order at a time, including across restarts.
   if(HasOpenAuroux()) { Print("AUROUX exposure already open, skipping signal ",id); return true; }
   bool buy=direction=="BUY";
   if(direction!="BUY" && direction!="SELL") return true;
   if(poi<=0 || sl<=0 || tp<=0 || (buy && !(sl<poi && poi<tp))
      || (!buy && !(tp<poi && poi<sl))) { Print("Invalid AUROUX levels ",id); return true; }
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick)) return false;
   ENUM_ORDER_TYPE side=buy?ORDER_TYPE_BUY:ORDER_TYPE_SELL;
   double current=buy?tick.ask:tick.bid;
   bool market=(orderType=="MARKET");
   double pricedEntry=market?current:poi;
   double originalRisk=MathAbs(poi-sl);
   double adverse=buy?(current-poi):(poi-current);
   if(market && (adverse>MaxAdversePOIFraction*originalRisk ||
      (buy && !(sl<current && current<tp)) ||
      (!buy && !(tp<current && current<sl)))) {
      Print("AUROUX market price outside safe POI/SL/TP bounds ",id);
      return true;
   }
   if(MaxAdversePOIFraction<0 || MaxAdversePOIFraction>1) {
      Print("AUROUX invalid adverse-slippage configuration ",id);
      return true;
   }
   if(!market) {
      if((direction=="BUY" && orderType!="BUY_LIMIT" && orderType!="BUY_STOP") ||
         (direction=="SELL" && orderType!="SELL_LIMIT" && orderType!="SELL_STOP")) return true;
      bool validPending=(orderType=="BUY_LIMIT" && poi<tick.ask) ||
                        (orderType=="BUY_STOP" && poi>tick.ask) ||
                        (orderType=="SELL_LIMIT" && poi>tick.bid) ||
                        (orderType=="SELL_STOP" && poi<tick.bid);
      if(!validPending) { Print("AUROUX invalid explicit pending order position ",id); return true; }
   }
   int expirationModes=(int)SymbolInfoInteger(_Symbol,SYMBOL_EXPIRATION_MODE);
   bool supportsExpiry=((expirationModes & SYMBOL_EXPIRATION_SPECIFIED)!=0);
   if(!market && !supportsExpiry) { Print("AUROUX pending expiration unsupported ",id); return true; }
   datetime brokerExpiry=(datetime)((long)TimeTradeServer()+deadline-(long)TimeGMT());
   if(!market && brokerExpiry<=(datetime)TimeTradeServer()) return true;
   double loss=0;
   if(!OrderCalcProfit(side,_Symbol,1.0,pricedEntry,sl,loss) || loss>=0) {
      Print("AUROUX invalid account-currency loss calculation ",id);
      return true;
   }
   double budget=AccountInfoDouble(ACCOUNT_BALANCE)*RiskPercent/100.0;
   if(RiskPercent<=0 || RiskPercent>4.0) { Print("AUROUX risk above 4% ceiling ",id); return true; }
   double volume=RoundedVolume(budget/(-loss));
   if(volume<=0 || volume*(-loss)>budget+0.00001) {
      Print("AUROUX volume exceeds risk budget ",id);
      return true;
   }
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetTypeFillingBySymbol(_Symbol);
   string note=SignalComment(id);
   bool success=false;
   if(market) {
      success=buy?trade.Buy(volume,_Symbol,0,sl,tp,note):trade.Sell(volume,_Symbol,0,sl,tp,note);
   } else if(orderType=="BUY_LIMIT") {
      success=trade.BuyLimit(volume,poi,_Symbol,sl,tp,ORDER_TIME_SPECIFIED,brokerExpiry,note);
   } else if(orderType=="BUY_STOP") {
      success=trade.BuyStop(volume,poi,_Symbol,sl,tp,ORDER_TIME_SPECIFIED,brokerExpiry,note);
   } else if(orderType=="SELL_LIMIT") {
      success=trade.SellLimit(volume,poi,_Symbol,sl,tp,ORDER_TIME_SPECIFIED,brokerExpiry,note);
   } else if(orderType=="SELL_STOP") {
      success=trade.SellStop(volume,poi,_Symbol,sl,tp,ORDER_TIME_SPECIFIED,brokerExpiry,note);
   }
   uint ret=trade.ResultRetcode();
   bool accepted=success && (ret==TRADE_RETCODE_DONE || ret==TRADE_RETCODE_PLACED || ret==TRADE_RETCODE_DONE_PARTIAL);
   if(!accepted) Print("AUROUX ",id," failed: ",ret," ",trade.ResultRetcodeDescription());
   else Print("AUROUX ",id," submitted ",direction," ",volume," lots at POI ",poi," SL ",sl," TP1 ",tp);
   return true; // Checkpoint rejected signals; never flood broker with retries.
}
long LatestId() {
   int handle=FileOpen(SignalFile,FILE_READ|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
   if(handle==INVALID_HANDLE) return 0;
   long latest=0;
   while(!FileIsEnding(handle)) {
      string id=FileReadString(handle);
      if(FileIsEnding(handle)) break;
      FileReadString(handle);FileReadString(handle);FileReadString(handle);FileReadString(handle);FileReadString(handle);FileReadString(handle);
      long n=(long)StringToInteger(id);
      if(n>latest) latest=n;
   }
   FileClose(handle);
   return latest;
}
int OnInit() {
   // Fail closed if this EA is attached to anything other than the approved
   // AUROUX demo account. Never trade the separate GWS REAL account.
   if(AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_DEMO ||
      AccountInfoInteger(ACCOUNT_LOGIN)!=ExpectedDemoLogin) {
      Print("AUROUX refused non-demo or unexpected account");
      return INIT_FAILED;
   }
   checkpoint="AUROUX_"+IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN))+"_"+IntegerToString((long)MagicNumber);
   if(!GlobalVariableCheck(checkpoint)) {
      int probe=FileOpen(SignalFile,FILE_READ|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
      if(probe!=INVALID_HANDLE) {
         FileClose(probe);
         long watermark=SkipHistoryOnFirstStart?LatestId():0;
         GlobalVariableSet(checkpoint,(double)watermark);
         Print("AUROUX initialized, starting after id ",watermark);
      } else Print("AUROUX waiting for initial bridge file; historical signals will not replay");
   }
   EventSetMillisecondTimer(MathMax(50,CheckMilliseconds));
   Print("AUROUX follower 1.11 initialized on demo account ",ExpectedDemoLogin);
   WriteHealth();
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason) { EventKillTimer(); }
void OnTimer() {
   WriteHealth();
   CancelExpiredPending();
   int handle=FileOpen(SignalFile,FILE_READ|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
   if(handle==INVALID_HANDLE) return;
   if(!GlobalVariableCheck(checkpoint)) {
      FileClose(handle);
      long watermark=SkipHistoryOnFirstStart?LatestId():0;
      GlobalVariableSet(checkpoint,(double)watermark);
      Print("AUROUX bridge connected, initial watermark ",watermark);
      return;
   }
   long checkpointId=(long)GlobalVariableGet(checkpoint);
   while(!FileIsEnding(handle)) {
      string id=FileReadString(handle);
      if(FileIsEnding(handle)) break;
      string direction=FileReadString(handle);
      double poi=StringToDouble(FileReadString(handle));
      double sl=StringToDouble(FileReadString(handle));
      double tp=StringToDouble(FileReadString(handle));
      long deadline=(long)StringToInteger(FileReadString(handle));
      string orderType=FileReadString(handle);
      long n=(long)StringToInteger(id);
      if(n<=checkpointId || n<=0) continue;
      if(!Execute(n,direction,poi,sl,tp,deadline,orderType)) break;
      checkpointId=n;
      GlobalVariableSet(checkpoint,(double)checkpointId);
      GlobalVariablesFlush();
   }
   FileClose(handle);
}
