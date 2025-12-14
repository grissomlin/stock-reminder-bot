# stock-reminder-bot
A Telegram bot utilizing Python, yfinance, and TA-Lib to perform technical analysis on a Google Sheet list of stock tickers and send timely alerts via the APScheduler. (中文：🤖 一個 Telegram 機器人，用於對 Google Sheets 內的股票代號進行技術分析，並在指標觸發時即時發送警報通知。)

下圖是 Telegram 傳送的技術指標通知截圖，顯示雲端系統對台股標的的即時分析與警報。
The image below shows a Telegram-based technical indicator alert screenshot, displaying real-time cloud-based analysis and signal notifications for Taiwan-listed ETFs.
![telegram1](image/telegram.png)


![telegram2](image/2.png)


以下是程式在 Railway 平台上執行時的 log 訊息截圖：
The following is a screenshot of the program’s log messages while running on the Railway platform:
![telegram3](image/3.png)



## 為什麼選擇 Google Sheets 作為操作介面？  
## Why Choose Google Sheets as the Operation Interface?

本專案使用 **Google Sheets** 作為主要操作介面，而不是建立獨立的資料庫或網頁系統，原因如下：  
This project uses **Google Sheets** as the main operation interface instead of building a separate database or web system, for the following reasons:
![googlesheet1](image/googlesheet1.png)

- **免建資料庫與網頁**  
  不需要額外開發後端資料庫或前端 CRUD 系統，降低維護成本。  
  **No need for database or web development**  
  No extra backend database or frontend CRUD system is required, reducing maintenance costs.

- **直觀的視覺化介面**  
  表格本身就是最簡單的 Dashboard，可以直接看到大盤狀態與技術指標。  
  **Intuitive visualization interface**  
  The spreadsheet itself serves as the simplest dashboard, directly showing market status and technical indicators.

- **操作方便**  
  新增或刪除股票只需在表格中增減列，修改通知開關只需編輯儲存格。  
  **Easy operation**  
  Adding or removing stocks only requires editing rows, and notification switches can be toggled by editing cells.

- **雲端同步與多人協作**  
  Google Sheets 天生支援多人同時編輯，無需額外的使用者管理系統。  
  **Cloud sync and collaboration**  
  Google Sheets natively supports multi-user editing without the need for an additional user management system.

- **輕量化、個人化的最佳解**  
  對個人或小型專案來說，Excel/Google Sheets 已經足夠，不必追求「高大上」的資料庫架構。  
  **Lightweight and personal-friendly solution**  
  For individuals or small projects, Excel/Google Sheets is sufficient without pursuing a complex database architecture.
![googlesheet2](image/googlesheet2.png)
👉 總結：Google Sheets 同時扮演 **資料存放處**、**操作介面**、**視覺化報表** 三種角色，讓系統設計更簡單、直觀且易於維護。  
👉 In summary: Google Sheets acts simultaneously as a **data repository**, **operation interface**, and **visual report**, making system design simpler, more intuitive, and easier to maintain.

### 舊版回顧 / Previous Version

📱 這張圖片是我幾年前開發的 **LINE Bot**，部署在 **Heroku** 平台，用來傳送股票技術指標的提醒訊息。  
後來因為 Heroku 開始收費，加上我專注在回測策略，這個版本就沒有再繼續維護。  

📱 This image shows the **LINE Bot** I developed a few years ago, deployed on the **Heroku** platform, which was used to send stock technical indicator alerts.  
Later, as Heroku introduced paid plans and I shifted my focus to backtesting strategies, this version was no longer maintained.


![googlesheet2](image/oldversion.png)


