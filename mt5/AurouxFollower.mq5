// AUROUX XAUUSD follower. Standalone from GWS. Compile in MetaEditor (MT5).
#property strict
#property version "1.00"
#include <Trade/Trade.mqh>
CTrade trade;
input string SignalFile = "auroux_signals.csv"; // MT5 Terminal Common/Files
input double RiskPercent = 4.0;
input bool RoundVolumeUp = true;
input ulong MagicNumber = 2601030;
input int EntryTolerancePoints = 10;
input int CheckMilliseconds = 100;
input bool SkipHistoryOnFirstStart = true;
string checkpoint;

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
bool Execute(long id,string direction,double poi,double sl,double tp,long deadline) {
   if(deadline<=0 || (long)TimeGMT()>=deadline) {
      Print("AUROUX expired signal ignored ",id);
      return true;
   }
   if(_Symbol!="XAUUSD" && _Symbol!="XAUUSD+") { Print("Attach to broker XAUUSD chart"); return false; }
   if(WasSubmitted(id)) return true;
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
   double tolerance=EntryTolerancePoints*SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   bool market=MathAbs(current-poi)<=tolerance;
   // Outside the POI tolerance, submit only a broker-expiring pending order.
   // A broker that cannot enforce expiration must not retain a GTC order.
   int expirationModes=(int)SymbolInfoInteger(_Symbol,SYMBOL_EXPIRATION_MODE);
   bool supportsExpiry=((expirationModes & SYMBOL_EXPIRATION_SPECIFIED)!=0);
   if(!market && !supportsExpiry) {
      Print("AUROUX pending rejected: broker does not support specified expiration ",id);
      return true;
   }
   double pricedEntry=market?current:poi;
   ENUM_ORDER_TYPE_TIME expiryType=ORDER_TIME_SPECIFIED;
   datetime brokerExpiry=(datetime)((long)TimeTradeServer() + deadline-(long)TimeGMT());
   if(!market && brokerExpiry<=(datetime)TimeTradeServer()) return true;
   double loss=0;
   if(!OrderCalcProfit(side,_Symbol,1.0,pricedEntry,sl,loss) || loss>=0) return false;
   double volume=RoundedVolume(AccountInfoDouble(ACCOUNT_BALANCE)*RiskPercent/100.0/(-loss));
   if(volume==0) { Print("Volume outside broker min/max: ",id); return true; }
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetTypeFillingBySymbol(_Symbol);
   string note=SignalComment(id);
   bool success=false;
   if(market) {
      success=buy?trade.Buy(volume,_Symbol,0,sl,tp,note):trade.Sell(volume,_Symbol,0,sl,tp,note);
   } else if(buy) {
      success=poi<current?trade.BuyLimit(volume,poi,_Symbol,sl,tp,expiryType,brokerExpiry,note)
                         :trade.BuyStop(volume,poi,_Symbol,sl,tp,expiryType,brokerExpiry,note);
   } else {
      success=poi>current?trade.SellLimit(volume,poi,_Symbol,sl,tp,expiryType,brokerExpiry,note)
                         :trade.SellStop(volume,poi,_Symbol,sl,tp,expiryType,brokerExpiry,note);
   }
   uint ret=trade.ResultRetcode();
   bool accepted=success && (ret==TRADE_RETCODE_DONE || ret==TRADE_RETCODE_PLACED || ret==TRADE_RETCODE_DONE_PARTIAL);
   if(!accepted) Print("AUROUX ",id," failed: ",ret," ",trade.ResultRetcodeDescription());
   else Print("AUROUX ",id," submitted ",direction," ",volume," lots at POI ",poi," SL ",sl," TP1 ",tp);
   return accepted;
}
long LatestId() {
   int handle=FileOpen(SignalFile,FILE_READ|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
   if(handle==INVALID_HANDLE) return 0;
   long latest=0;
   while(!FileIsEnding(handle)) {
      string id=FileReadString(handle);
      if(FileIsEnding(handle)) break;
      FileReadString(handle);FileReadString(handle);FileReadString(handle);FileReadString(handle);FileReadString(handle);
      long n=(long)StringToInteger(id);
      if(n>latest) latest=n;
   }
   FileClose(handle);
   return latest;
}
int OnInit() {
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
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason) { EventKillTimer(); }
void OnTimer() {
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
      long n=(long)StringToInteger(id);
      if(n<=checkpointId || n<=0) continue;
      if(!Execute(n,direction,poi,sl,tp,deadline)) break;
      checkpointId=n;
      GlobalVariableSet(checkpoint,(double)checkpointId);
      GlobalVariablesFlush();
   }
   FileClose(handle);
}
