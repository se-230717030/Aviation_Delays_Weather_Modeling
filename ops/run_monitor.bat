@echo off
cd /d "C:\Users\Ali\Desktop\Aviation_Delays_Weather_Modeling"
if not exist logs mkdir logs
C:\Python314\python.exe src\monitor.py >> logs\monitor.log 2>&1