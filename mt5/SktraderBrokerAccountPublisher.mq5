// Read-only broker account/position/deal telemetry for AUROUX DEMO.
// No OrderSend, CTrade, OrderModify, position close or account switching.
#property strict
#property version "1.00"
input bool RequireDemo = true;
input int IntervalSeconds = 5;
input int DealHistoryDays = 7;
input string SnapshotFile = "sktrader_demo_account.csv";
input string PositionFile = "sktrader_demo_positions.csv";
input string DealFile = "sktrader_demo_deals.csv";

bool Eligible() {
   return !RequireDemo || AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO;
}
int OnInit() {
   if(!Eligible()) {
      Print("Read-only broker publisher refused REAL account");
      return INIT_FAILED;
   }
   if(IntervalSeconds<1 || DealHistoryDays<1 || DealHistoryDays>30)
      return INIT_PARAMETERS_INCORRECT;
   EventSetTimer(IntervalSeconds);
   Print("Read-only broker account publisher started");
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason) { EventKillTimer(); }

bool AtomicFile(const string temporary,const string destination) {
   return FileMove(temporary,FILE_COMMON,destination,FILE_COMMON|FILE_REWRITE);
}
void OnTimer() {
   if(!Eligible()) return;
   string tmp=SnapshotFile+".tmp";
   int f=FileOpen(tmp,FILE_WRITE|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
   if(f==INVALID_HANDLE) { Print("account CSV open failed ",GetLastError());return; }
   FileWrite(f,"login","mode","currency","server","generated_at",
             "balance","equity","margin","margin_free","floating_profit",
             "positions","orders");
   FileWrite(f,(string)AccountInfoInteger(ACCOUNT_LOGIN),
             AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO?"DEMO":"REAL",
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
