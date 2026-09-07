@echo off
REM Download historical data. Edit the symbol list to taste.
setlocal
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
set SYMS=RELIANCE TCS HDFCBANK INFY ICICIBANK SBIN AXISBANK ITC LT NIFTY50 BANKNIFTY
%PY% -m app.data.seed --symbols %SYMS% --timeframe 1d --period 10y
%PY% -m app.data.seed --symbols %SYMS% --timeframe 15m
%PY% -m app.data.seed --symbols %SYMS% --timeframe 5m
echo.
echo Done. Intraday history from Yahoo is capped at ~60 days; for more,
echo export CSV from your broker or MT5 into the data\ folder as SYMBOL_TF.csv
pause
