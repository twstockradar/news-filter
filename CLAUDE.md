# NewsFilter — 每日選聞(台股新聞篩選)

抓鉅亨網台股新聞,依「內文重點(7類) × 公司高層」**交集**條件過濾,產生公開靜態網頁。

## 線上
- 公開網站:https://twstockradar.github.io/news-filter/(GitHub Pages,來源 = `main` 分支 `/docs`)
- Repo:github.com/twstockradar/news-filter(Public;擁有者以 Organization 隱藏)
- commit 一律用中性身分 `data-bot`(本 repo 已設 local git config,**勿改回個人身分/email**)

## 篩選邏輯(核心需求)
- 條件 A(內容):內文/標題命中 `keywords.json` 七大類關鍵字之一(漲價缺貨/新產品/國家政策/產業超成長/高資本支出/技術領先/國際熱賣)
- 條件 B(來源):內文出現「執行長/董事長/總經理/副總經理/財務長/公司指出」之一,**或**標題含「澄清」
- 只收錄 A∧B 都滿足的新聞
- **UI 與文案絕不出現「六人說」「七要訣」字樣**(硬性需求);介面用「內文重點」「公司高層」稱呼

## 架構
- `fetch_news.py` — 打 cnyes newslist API(列表直接附全文)。原始文章累積存 `data/raw.json`(gitignored);**每次都從 raw 重新套規則**,所以改 `keywords.json` 後重跑即全檔套用。`--rescan` 只重套規則不抓新聞。
- 相似新聞去重規則寫在 fetch_news.py 常數區註解(同日同事件併入 `dupes`,門檻是用 30 天實測校準的,改之前先看註解)。
- 增量更新:以最大 publishAt 往後抓(留 2 小時重疊);**過濾結果無變化就不重寫 data.js、不 push**。
- `run_update.bat` — fetch → 若 `docs/data.js` 有變更才 commit + push。
- Windows 工作排程「NewsFilter Update」每小時跑(電池也跑、錯過補跑)。
- `docs/` — 純靜態:`index.html` / `app.js` / `styles.css` / `data.js`(`window.NEWS_DATA=...`)/ `.nojekyll`。

## 常見任務
- 立即更新:`python fetch_news.py` 或點 `run_update.bat`
- 調整關鍵字:改 `keywords.json` → `python fetch_news.py --rescan` → 確認分類數量合理 → commit+push
- 往回補歷史:`FETCH_OLDER_DAYS=N python fetch_news.py`(cnyes API 支援 startAt/endAt 任意區間)
- 改網頁:編輯 docs/ → `node --check docs/app.js` → 本機預覽(主專案 AlfieLoader 的 launch.json 有 `newsfilter-docs` 設定)→ push

## 重點 / 雷
- 日期一律 Asia/Taipei;前端日期用本地欄位組字串,**不要用 `toISOString()`**。
- `fetch_news.py` 已強制 UTF-8 輸出(避免排程 cp950 崩潰)。
- cnyes API 的 `content` 是 escape 過的 HTML;`stock` 欄是代號字串(US 股有 `US-` 前綴,去重時只認台股代號,理由見程式註解)。
- `data/`(原始全文)永不進 git;`docs/data.js` 是瘦身版(無全文),搜尋範圍 = 標題+標籤+個股+摘要。
- 預覽沙箱截圖會 timeout(環境問題,非頁面問題);驗證用 preview_eval / snapshot / inspect。
