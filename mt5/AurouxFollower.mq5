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
input int CheckSeconds = 2;
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
bool Execute(long id,string direction,double poi,double sl,double tp) {
   if(_Symbol!="XAUUSD" && StringFind(_Symbol,"XAUUSD")!=0) { Print("Attach to broker XAUUSD chart"); return false; }
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
   double loss=0;
   if(!OrderCalcProfit(side,_Symbol,1.0,poi,sl,loss) || loss>=0) return false;
   double volume=RoundedVolume(AccountInfoDouble(ACCOUNT_BALANCE)*RiskPercent/100.0/(-loss));
   if(volume==0) { Print("Volume outside broker min/max: ",id); return true; }
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetTypeFillingBySymbol(_Symbol);
   string note=SignalComment(id);
   double current=buy?tick.ask:tick.bid;
   double tolerance=EntryTolerancePoints*SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   bool success=false;
   if(MathAbs(current-poi)<=tolerance) {
      success=buy?trade.Buy(volume,_Symbol,0,sl,tp,note):trade.Sell(volume,_Symbol,0,sl,tp,note);
   } else if(buy) {
      success=poi<current?trade.BuyLimit(volume,poi,_Symbol,sl,tp,ORDER_TIME_GTC,0,note)
                         :trade.BuyStop(volume,poi,_Symbol,sl,tp,ORDER_TIME_GTC,0,note);
   } else {
      success=poi>current?trade.SellLimit(volume,poi,_Symbol,sl,tp,ORDER_TIME_GTC,0,note)
                         :trade.SellStop(volume,poi,_Symbol,sl,tp,ORDER_TIME_GTC,0,note);
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
      FileReadString(handle);FileReadString(handle);FileReadString(handle);FileReadString(handle);
      long n=(long)StringToInteger(id);
      if(n>latest) latest=n;
   }
   FileClose(handle);
   return latest;
}
int OnInit() {
   checkpoint="AUROUX_"+IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN))+"_"+IntegerToString((long)MagicNumber);
   if(!GlobalVariableCheck(checkpoint)) {
      long watermark=SkipHistoryOnFirstStart?LatestId():0;
      GlobalVariableSet(checkpoint,(double)watermark);
      Print("AUROUX initialized, starting after id ",watermark);
   }
   EventSetTimer(MathMax(1,CheckSeconds));
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason) { EventKillTimer(); }
void OnTimer() {
   int handle=FileOpen(SignalFile,FILE_READ|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
   if(handle==INVALID_HANDLE) return;
   long checkpointId=(long)GlobalVariableGet(checkpoint);
   while(!FileIsEnding(handle)) {
      string id=FileReadString(handle);
      if(FileIsEnding(handle)) break;
      string direction=FileReadString(handle);
      double poi=StringToDouble(FileReadString(handle));
      double sl=StringToDouble(FileReadString(handle));
      double tp=StringToDouble(FileReadString(handle));
      long n=(long)StringToInteger(id);
      if(n<=checkpointId || n<=0) continue;
      if(!Execute(n,direction,poi,sl,tp)) break;
      checkpointId=n;
      GlobalVariableSet(checkpoint,(double)checkpointId);
      GlobalVariablesFlush();
   }
   FileClose(handle);
}
