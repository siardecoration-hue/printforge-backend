<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PrintForge - AI ile 3D Model Uretici</title>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800;900&family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<script type="module" src="https://unpkg.com/@google/model-viewer@3.5.0/dist/model-viewer.min.js"></script>
<style>
:root{--bg:#04080a;--bg2:#070d10;--border:#0e2028;--accent:#00e5ff;--accent2:#00ff9d;--text:#c8dde5;--muted:#2a4a5a;--card:#060c10;--red:#ff4466;--orange:#ffaa00;--purple:#a855f7;--glass:rgba(6,12,16,0.85)}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;-webkit-font-smoothing:antialiased;overflow-x:hidden}
h1,h2,h3,h4{font-family:'Outfit',sans-serif}
::-webkit-scrollbar{width:4px}::-webkit-scrollbar-thumb{background:var(--muted);border-radius:4px}
#bgCanvas{position:fixed;inset:0;z-index:0;pointer-events:none}
.bg-gradient{position:fixed;inset:0;z-index:0;pointer-events:none;background:radial-gradient(ellipse 60% 50% at 20% 20%,rgba(0,229,255,0.04),transparent),radial-gradient(ellipse 50% 40% at 80% 80%,rgba(0,255,157,0.03),transparent)}
.bg-grid{position:fixed;inset:0;z-index:0;pointer-events:none;background-image:linear-gradient(rgba(0,229,255,0.015) 1px,transparent 1px),linear-gradient(90deg,rgba(0,229,255,0.015) 1px,transparent 1px);background-size:60px 60px}
.scan-line{position:fixed;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(0,229,255,0.04),transparent);animation:scanLine 8s linear infinite;z-index:0;pointer-events:none}
@keyframes scanLine{from{top:-1px}to{top:100vh}}
.nav{position:sticky;top:0;z-index:100;display:flex;justify-content:space-between;align-items:center;padding:12px 24px;background:var(--glass);backdrop-filter:blur(24px);border-bottom:1px solid rgba(0,229,255,0.06);gap:10px;flex-wrap:wrap}
.nav-logo{display:flex;align-items:center;gap:8px;text-decoration:none}
.nlm{width:22px;height:22px;border:1.5px solid var(--accent);transform:rotate(45deg);display:flex;align-items:center;justify-content:center;transition:all 0.3s}
.nav-logo:hover .nlm{border-color:var(--accent2)}
.nli{width:6px;height:6px;background:var(--accent);transform:rotate(-45deg)}
.nlt{font-family:'Outfit',sans-serif;font-size:15px;font-weight:800;color:var(--accent);letter-spacing:0.08em}
.nav-right{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.nav-status{font-size:8px;color:var(--muted);display:flex;align-items:center;gap:4px;padding:4px 10px;border:1px solid var(--border);border-radius:20px}
.nav-dot{width:5px;height:5px;border-radius:50%;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
.nav-user{display:flex;align-items:center;gap:8px}
.nav-avatar{width:30px;height:30px;border-radius:50%;background:linear-gradient(135deg,rgba(0,229,255,0.2),rgba(168,85,247,0.2));border:1.5px solid var(--accent);display:flex;align-items:center;justify-content:center;font-size:12px;color:var(--accent);cursor:pointer;font-family:'Outfit',sans-serif;font-weight:700}
.nav-uname{font-size:10px;color:var(--text);font-weight:500}
.nav-usage{font-size:8px;color:var(--accent2);background:rgba(0,255,157,0.06);padding:3px 10px;border:1px solid rgba(0,255,157,0.15);border-radius:20px;font-weight:600}
.nbtn{padding:6px 14px;font-family:'Inter',sans-serif;font-size:9px;letter-spacing:0.08em;cursor:pointer;transition:all 0.2s;border:1px solid var(--border);background:transparent;color:var(--text);border-radius:8px;font-weight:500}
.nbtn:hover{border-color:var(--accent);color:var(--accent)}
.nbtn.accent{background:linear-gradient(135deg,var(--accent),#0099cc);color:#04080a;border:none;font-weight:600}
.nbtn.accent:hover{background:linear-gradient(135deg,var(--accent2),var(--accent))}
.nbtn.red{color:var(--red);border-color:rgba(255,68,102,0.2)}
.banner{padding:8px 20px;text-align:center;font-size:9px;display:none;position:relative;z-index:1}
.banner.demo{background:rgba(255,170,0,0.06);color:var(--orange);border-bottom:1px solid rgba(255,170,0,0.1)}
.banner.usage{background:rgba(0,255,157,0.04);color:var(--accent2);border-bottom:1px solid rgba(0,255,157,0.1)}
.banner.verify{background:rgba(255,170,0,0.06);color:var(--orange);border-bottom:1px solid rgba(255,170,0,0.1)}
.banner a{color:var(--accent);cursor:pointer;text-decoration:underline}
.container{position:relative;z-index:1;max-width:920px;margin:0 auto;padding:28px 20px 80px}

/* AUTH */
.auth-overlay{display:none;position:fixed;inset:0;z-index:200;background:rgba(4,8,10,0.94);backdrop-filter:blur(8px);align-items:center;justify-content:center;padding:20px}
.auth-overlay.on{display:flex}
.auth-box{background:var(--card);border:1px solid var(--border);padding:36px 30px;width:100%;max-width:400px;position:relative;border-radius:20px;box-shadow:0 24px 64px rgba(0,0,0,0.4)}
.auth-close{position:absolute;top:14px;right:16px;background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer}
.auth-logo{text-align:center;margin-bottom:20px;font-family:'Outfit',sans-serif;font-size:20px;font-weight:800;color:var(--accent)}
.auth-tabs{display:flex;border:1px solid var(--border);margin-bottom:18px;border-radius:10px;overflow:hidden}
.auth-tab{flex:1;padding:10px;background:transparent;border:none;color:var(--muted);font-family:'Inter',sans-serif;font-size:10px;cursor:pointer;font-weight:500}
.auth-tab.on{background:rgba(0,229,255,0.06);color:var(--accent)}
.fg{margin-bottom:12px}
.fg label{font-size:9px;letter-spacing:0.1em;color:var(--muted);margin-bottom:5px;display:block;font-weight:500}
.fg input{width:100%;background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:11px 14px;font-size:13px;font-family:'Inter',sans-serif;border-radius:10px}
.fg input:focus{outline:none;border-color:rgba(0,229,255,0.4)}
.fg input::placeholder{color:var(--muted)}
.auth-btn{width:100%;padding:13px;background:linear-gradient(135deg,var(--accent),#0099cc);color:#04080a;border:none;font-family:'Inter',sans-serif;font-size:12px;cursor:pointer;margin-top:8px;border-radius:10px;font-weight:700}
.auth-btn:hover{background:linear-gradient(135deg,var(--accent2),var(--accent))}
.auth-divider{display:flex;align-items:center;gap:12px;margin:16px 0;color:var(--muted);font-size:9px}
.auth-divider::before,.auth-divider::after{content:'';flex:1;height:1px;background:var(--border)}
.google-btn{width:100%;padding:11px;background:transparent;border:1px solid var(--border);color:var(--text);font-family:'Inter',sans-serif;font-size:11px;cursor:pointer;border-radius:10px;display:flex;align-items:center;justify-content:center;gap:10px;font-weight:500}
.google-btn:hover{border-color:var(--accent)}
.google-btn svg{width:16px;height:16px}
.auth-msg{padding:8px 12px;font-size:10px;margin-bottom:10px;display:none;border-radius:8px;line-height:1.6}
.auth-msg.err{background:rgba(255,68,102,0.08);border:1px solid rgba(255,68,102,0.2);color:var(--red);display:block}
.auth-msg.ok{background:rgba(0,255,157,0.08);border:1px solid rgba(0,255,157,0.2);color:var(--accent2);display:block}
.auth-footer{text-align:center;margin-top:14px;font-size:9px;color:var(--muted)}
.auth-footer a{color:var(--accent);cursor:pointer}
.auth-link{font-size:10px;color:var(--accent);cursor:pointer;text-align:center;display:block;margin-top:10px}

/* TABS */
.tabs{display:flex;border:1px solid var(--border);margin-bottom:24px;border-radius:12px;overflow:hidden;background:var(--glass)}
.tab{flex:1;padding:12px 6px;background:transparent;border:none;color:var(--muted);font-family:'Inter',sans-serif;font-size:9px;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:4px;font-weight:500;position:relative}
.tab.on{color:var(--accent)}
.tab.on::after{content:'';position:absolute;bottom:0;left:15%;right:15%;height:2px;background:var(--accent);border-radius:1px}
.tab:hover:not(.on){background:rgba(0,229,255,0.02);color:var(--text)}
.panel{display:none}.panel.on{display:block}
.card{background:var(--glass);border:1px solid var(--border);padding:26px;margin-bottom:14px;border-radius:16px}
.label{font-size:9px;letter-spacing:0.12em;color:var(--muted);margin-bottom:6px;display:block;font-weight:600}
textarea{width:100%;background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:12px;font-size:13px;font-family:'Inter',sans-serif;resize:vertical;min-height:70px;border-radius:12px}
textarea:focus{outline:none;border-color:rgba(0,229,255,0.4)}
textarea::placeholder{color:var(--muted)}
.examples{margin-top:10px;display:flex;gap:5px;flex-wrap:wrap}
.ex-btn{padding:6px 12px;border:1px solid var(--border);background:transparent;color:var(--muted);font-family:'Inter',sans-serif;font-size:9px;cursor:pointer;border-radius:8px;font-weight:500}
.ex-btn:hover{border-color:var(--accent);color:var(--accent)}
.style-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:6px}
.style-opt{padding:12px 8px;border:1px solid var(--border);background:transparent;color:var(--muted);font-family:'Inter',sans-serif;font-size:9px;cursor:pointer;text-align:center;border-radius:12px;font-weight:500}
.style-opt:hover{border-color:rgba(0,229,255,0.3);color:var(--text)}
.style-opt.on{border-color:var(--accent);color:var(--accent);background:rgba(0,229,255,0.04)}
.style-opt .ico{font-size:18px;display:block;margin-bottom:4px}
.upload{border:2px dashed var(--border);padding:36px 20px;text-align:center;cursor:pointer;position:relative;overflow:hidden;border-radius:16px}
.upload:hover,.upload.drag{border-color:var(--accent)}
.upload.has{border-color:var(--accent2);border-style:solid}
.upload input{position:absolute;inset:0;opacity:0;cursor:pointer}
.upload .ico{font-size:30px;margin-bottom:8px;color:var(--accent)}
.upload p{font-size:11px;color:var(--muted)}
.preview{margin-top:14px;display:none;position:relative}.preview.on{display:block}
.preview img{max-width:100%;max-height:200px;display:block;margin:0 auto;border:1px solid var(--border);border-radius:12px}
.preview .rm{position:absolute;top:6px;right:6px;width:26px;height:26px;background:rgba(255,68,102,0.85);border:none;color:#fff;border-radius:50%;cursor:pointer;font-size:11px}
.gen-btn{width:100%;padding:14px;background:linear-gradient(135deg,var(--accent),#0099cc);color:#04080a;border:none;font-family:'Inter',sans-serif;font-size:12px;cursor:pointer;font-weight:700;margin-top:14px;border-radius:12px}
.gen-btn:hover:not(:disabled){background:linear-gradient(135deg,var(--accent2),var(--accent))}
.gen-btn:disabled{opacity:0.4;cursor:not-allowed}
.sec{display:none;margin-bottom:20px}.sec.on{display:block}
.prog-card{background:var(--glass);border:1px solid var(--border);padding:24px;border-radius:16px}
.prog-top{display:flex;justify-content:space-between;margin-bottom:14px}
.prog-title{font-family:'Outfit',sans-serif;font-size:15px;font-weight:700}
.prog-pct{font-family:'Outfit',sans-serif;font-size:22px;font-weight:800;color:var(--accent)}
.prog-bar-bg{width:100%;height:6px;background:var(--bg2);overflow:hidden;margin-bottom:10px;border-radius:3px}
.prog-bar{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));width:0%;transition:width 0.5s;border-radius:3px}
.prog-step{font-size:10px;color:var(--muted);display:flex;align-items:center;gap:6px}
.spinner{display:inline-block;width:10px;height:10px;border:2px solid var(--muted);border-top-color:var(--accent);border-radius:50%;animation:spin 0.8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.result-card{background:var(--glass);border:1px solid var(--accent2);padding:24px;text-align:center;border-radius:16px}
.result-card h3{font-family:'Outfit',sans-serif;font-size:20px;font-weight:800;margin-bottom:6px}
.result-card>p{font-size:11px;color:var(--muted);margin-bottom:16px}
.viewer{width:100%;height:360px;background:var(--bg2);border:1px solid var(--border);margin-bottom:16px;overflow:hidden;display:flex;align-items:center;justify-content:center;border-radius:14px}
.viewer model-viewer{width:100%;height:100%}
.dl-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:10px}
.dl-btn{padding:12px 8px;border:1px solid var(--border);background:var(--card);color:var(--text);font-family:'Inter',sans-serif;font-size:10px;cursor:pointer;text-decoration:none;text-align:center;display:flex;flex-direction:column;align-items:center;gap:3px;border-radius:10px;font-weight:500}
.dl-btn:hover{border-color:var(--accent);color:var(--accent)}
.dl-btn .dl-fmt{font-size:7px;color:var(--muted)}
.dl-btn.primary{border-color:var(--accent2);background:rgba(0,255,157,0.05)}
.new-btn{width:100%;padding:11px;border:1px solid var(--border);background:transparent;color:var(--text);font-family:'Inter',sans-serif;font-size:10px;cursor:pointer;border-radius:10px;font-weight:500}
.new-btn:hover{border-color:var(--accent);color:var(--accent)}
.err-card{background:rgba(255,68,102,0.04);border:1px solid rgba(255,68,102,0.15);padding:24px;text-align:center;border-radius:16px}
.err-card h3{color:var(--red);font-size:15px;margin-bottom:6px}
.err-card p{font-size:10px;color:var(--muted);margin-bottom:14px}

/* GALLERY */
.gal-toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:16px}
.gal-toolbar input{flex:1;min-width:140px;background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:10px 14px;font-family:'Inter',sans-serif;font-size:12px;border-radius:10px}
.gal-toolbar select{background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:10px;font-family:'Inter',sans-serif;font-size:11px;border-radius:10px}
.gal-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px}
.gal-card{background:var(--card);border:1px solid var(--border);cursor:pointer;border-radius:16px;overflow:hidden;transition:all 0.25s}
.gal-card:hover{border-color:rgba(0,229,255,0.25);transform:translateY(-4px);box-shadow:0 16px 40px rgba(0,0,0,0.3)}
.gal-thumb{height:180px;background:var(--bg2);overflow:hidden;display:flex;align-items:center;justify-content:center;position:relative}
.gal-thumb model-viewer{width:100%;height:100%}
.gal-badge{position:absolute;top:8px;left:8px;background:var(--glass);border:1px solid rgba(0,229,255,0.15);padding:3px 8px;font-size:8px;color:var(--accent);border-radius:6px;font-weight:600}
.gal-body{padding:14px}
.gal-title{font-family:'Outfit',sans-serif;font-size:14px;font-weight:700;margin-bottom:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gal-meta{font-size:9px;color:var(--muted);display:flex;justify-content:space-between;margin-bottom:8px}
.gal-stats{display:flex;gap:14px;margin-bottom:10px}
.gal-stat{font-size:9px;color:var(--muted)}.gal-stat span{color:var(--accent2);font-weight:600}
.gal-actions{display:flex;gap:5px}
.gal-btn{flex:1;padding:7px;border:1px solid var(--border);background:transparent;color:var(--text);font-family:'Inter',sans-serif;font-size:9px;cursor:pointer;text-align:center;border-radius:8px;text-decoration:none;display:flex;align-items:center;justify-content:center;font-weight:500}
.gal-btn:hover{border-color:var(--accent);color:var(--accent)}
.gal-btn.liked{color:var(--red);border-color:rgba(255,68,102,0.3)}
.gal-btn.dl{background:rgba(0,229,255,0.04);border-color:rgba(0,229,255,0.15)}
.gal-empty{text-align:center;padding:60px 20px;color:var(--muted);font-size:12px;grid-column:1/-1}

/* DETAIL */
.detail-overlay{display:none;position:fixed;inset:0;z-index:150;background:rgba(4,8,10,0.96);overflow-y:auto;padding:20px}
.detail-overlay.on{display:block}
.detail-container{max-width:900px;margin:0 auto}
.detail-back{display:inline-flex;align-items:center;gap:6px;color:var(--muted);font-size:11px;cursor:pointer;margin-bottom:20px;padding:8px 16px;border:1px solid var(--border);background:transparent;border-radius:10px;font-family:'Inter',sans-serif;font-weight:500}
.detail-back:hover{border-color:var(--accent);color:var(--accent)}
.detail-main{display:grid;grid-template-columns:1.3fr 1fr;gap:24px;margin-bottom:24px}
.detail-viewer{background:var(--bg2);border:1px solid var(--border);border-radius:16px;overflow:hidden;height:420px}
.detail-viewer model-viewer{width:100%;height:100%}
.detail-info{display:flex;flex-direction:column}
.detail-title{font-family:'Outfit',sans-serif;font-size:24px;font-weight:800;margin-bottom:8px}
.detail-author{font-size:12px;color:var(--muted);margin-bottom:16px;display:flex;align-items:center;gap:8px}
.detail-author-avatar{width:26px;height:26px;border-radius:50%;background:linear-gradient(135deg,rgba(0,229,255,0.2),rgba(168,85,247,0.2));border:1px solid var(--accent);display:flex;align-items:center;justify-content:center;font-size:10px;color:var(--accent);font-weight:700}
.detail-stats-row{display:flex;gap:16px;margin-bottom:18px;padding:14px;background:var(--bg2);border-radius:12px}
.detail-stat{text-align:center;flex:1}
.detail-stat-num{font-family:'Outfit',sans-serif;font-size:20px;font-weight:800;color:var(--accent)}
.detail-stat-lbl{font-size:7px;color:var(--muted);letter-spacing:0.12em;margin-top:2px}
.detail-section{margin-bottom:14px}
.detail-section-title{font-size:9px;letter-spacing:0.12em;color:var(--muted);margin-bottom:8px;font-weight:600}
.detail-tags{display:flex;gap:6px;flex-wrap:wrap}
.detail-tag{padding:4px 10px;background:rgba(0,229,255,0.04);border:1px solid rgba(0,229,255,0.12);color:var(--accent);font-size:9px;border-radius:8px;font-weight:500}
.detail-prompt{background:var(--bg2);border:1px solid var(--border);padding:12px 16px;font-size:12px;color:var(--text);line-height:1.7;border-radius:12px;font-style:italic}
.detail-dl-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:8px}
.detail-dl{padding:14px;border:1px solid var(--border);background:var(--card);text-align:center;cursor:pointer;text-decoration:none;color:var(--text);border-radius:12px;font-weight:500}
.detail-dl:hover{border-color:var(--accent);color:var(--accent)}
.detail-dl .dl-name{font-size:12px;font-weight:600}
.detail-dl .dl-desc{font-size:8px;color:var(--muted);margin-top:2px}
.detail-dl.primary{border-color:var(--accent2);background:rgba(0,255,157,0.04)}
.detail-like-btn{width:100%;padding:12px;border:1px solid var(--border);background:transparent;color:var(--text);font-family:'Inter',sans-serif;font-size:12px;cursor:pointer;margin-top:10px;border-radius:12px;display:flex;align-items:center;justify-content:center;gap:8px;font-weight:600}
.detail-like-btn:hover{border-color:var(--red);color:var(--red)}
.detail-like-btn.liked{background:rgba(255,68,102,0.06);border-color:var(--red);color:var(--red)}

/* COMMENTS */
.comments-section{margin-bottom:24px}
.comments-title{font-family:'Outfit',sans-serif;font-size:16px;font-weight:700;margin-bottom:14px;display:flex;align-items:center;gap:8px}
.comments-title::before{content:'';width:3px;height:16px;background:var(--accent);border-radius:2px}
.comment-input{display:flex;gap:8px;margin-bottom:16px}
.comment-input textarea{min-height:40px;flex:1;border-radius:10px;font-size:12px;padding:10px}
.comment-send{padding:10px 20px;background:var(--accent);color:#04080a;border:none;border-radius:10px;font-family:'Inter',sans-serif;font-size:11px;cursor:pointer;font-weight:600;align-self:flex-end}
.comment-send:hover{background:var(--accent2)}
.comment-list{display:flex;flex-direction:column;gap:10px}
.comment-item{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:14px}
.comment-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.comment-author{font-size:11px;font-weight:600;color:var(--accent)}
.comment-date{font-size:9px;color:var(--muted)}
.comment-text{font-size:12px;line-height:1.6;color:var(--text)}
.comment-delete{background:none;border:none;color:var(--red);font-size:9px;cursor:pointer;margin-top:6px}

/* COLLECTION ADD */
.col-add-btn{padding:8px 14px;border:1px solid var(--border);background:transparent;color:var(--muted);font-family:'Inter',sans-serif;font-size:9px;cursor:pointer;border-radius:8px;margin-top:8px;width:100%;text-align:center}
.col-add-btn:hover{border-color:var(--accent);color:var(--accent)}
.col-dropdown{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:10px;margin-top:8px;display:none}
.col-dropdown.on{display:block}
.col-dropdown-item{padding:8px 12px;cursor:pointer;font-size:11px;border-radius:6px;display:flex;justify-content:space-between}
.col-dropdown-item:hover{background:rgba(0,229,255,0.06);color:var(--accent)}
.col-new-input{display:flex;gap:6px;margin-top:8px}
.col-new-input input{flex:1;background:var(--card);border:1px solid var(--border);color:var(--text);padding:8px 12px;font-size:11px;font-family:'Inter',sans-serif;border-radius:8px}
.col-new-input button{padding:8px 14px;background:var(--accent);color:#04080a;border:none;border-radius:8px;font-size:10px;cursor:pointer;font-weight:600}

/* SIMILAR */
.similar-section{margin-bottom:40px}
.similar-title{font-family:'Outfit',sans-serif;font-size:18px;font-weight:700;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.similar-title::before{content:'';width:3px;height:18px;background:linear-gradient(var(--accent),var(--accent2));border-radius:2px}
.similar-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
.similar-card{background:var(--card);border:1px solid var(--border);border-radius:14px;overflow:hidden;cursor:pointer}
.similar-card:hover{border-color:rgba(0,229,255,0.25);transform:translateY(-2px)}
.similar-thumb{height:130px;background:var(--bg2);overflow:hidden}
.similar-thumb model-viewer{width:100%;height:100%}
.similar-body{padding:10px 12px}
.similar-name{font-family:'Outfit',sans-serif;font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.similar-meta{font-size:8px;color:var(--muted);margin-top:3px}

/* COLLECTIONS */
.col-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;margin-top:16px}
.col-card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:20px;cursor:pointer;transition:all 0.2s}
.col-card:hover{border-color:rgba(0,229,255,0.25);transform:translateY(-2px)}
.col-card-name{font-family:'Outfit',sans-serif;font-size:16px;font-weight:700;margin-bottom:4px}
.col-card-desc{font-size:10px;color:var(--muted);margin-bottom:10px;line-height:1.5}
.col-card-meta{font-size:9px;color:var(--muted);display:flex;justify-content:space-between}
.col-card-count{color:var(--accent);font-weight:600}
.col-create-card{background:transparent;border:2px dashed var(--border);border-radius:16px;padding:30px 20px;cursor:pointer;text-align:center;transition:all 0.2s}
.col-create-card:hover{border-color:var(--accent)}
.col-create-card .ico{font-size:28px;color:var(--accent);margin-bottom:8px}
.col-create-card p{font-size:11px;color:var(--muted)}
.col-detail-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:10px}
.col-detail-title{font-family:'Outfit',sans-serif;font-size:20px;font-weight:800}
.col-detail-desc{font-size:11px;color:var(--muted);margin-bottom:16px}

/* BLOG */
.blog-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;margin-top:16px}
.blog-card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:24px;cursor:pointer;transition:all 0.2s}
.blog-card:hover{border-color:rgba(0,229,255,0.25);transform:translateY(-2px)}
.blog-cat{font-size:8px;color:var(--accent);letter-spacing:0.12em;font-weight:600;margin-bottom:8px;text-transform:uppercase}
.blog-card-title{font-family:'Outfit',sans-serif;font-size:16px;font-weight:700;margin-bottom:8px;line-height:1.3}
.blog-card-summary{font-size:11px;color:var(--muted);line-height:1.6;margin-bottom:12px}
.blog-card-meta{font-size:9px;color:var(--muted);display:flex;gap:16px}
.blog-detail{max-width:700px;margin:0 auto}
.blog-detail-title{font-family:'Outfit',sans-serif;font-size:28px;font-weight:800;margin-bottom:12px;line-height:1.2}
.blog-detail-meta{font-size:10px;color:var(--muted);margin-bottom:24px;display:flex;gap:16px}
.blog-detail-content{font-size:14px;line-height:1.9;color:var(--text)}
.blog-detail-content h2{font-family:'Outfit',sans-serif;font-size:20px;font-weight:700;color:var(--accent);margin:28px 0 12px}
.blog-detail-content h3{font-family:'Outfit',sans-serif;font-size:16px;font-weight:600;margin:20px 0 8px}
.blog-detail-content p{margin-bottom:14px}
.blog-detail-content strong{color:var(--accent2)}
.blog-back{display:inline-flex;align-items:center;gap:6px;color:var(--muted);font-size:11px;cursor:pointer;margin-bottom:24px;padding:8px 16px;border:1px solid var(--border);background:transparent;border-radius:10px;font-weight:500}
.blog-back:hover{border-color:var(--accent);color:var(--accent)}

/* PROFILE */
.profile-header{background:linear-gradient(135deg,rgba(0,229,255,0.06),rgba(168,85,247,0.04));border:1px solid var(--border);border-radius:20px;padding:30px;margin-bottom:20px;position:relative;overflow:hidden}
.profile-header::before{content:'';position:absolute;inset:0;background-image:linear-gradient(rgba(0,229,255,0.03) 1px,transparent 1px),linear-gradient(90deg,rgba(0,229,255,0.03) 1px,transparent 1px);background-size:30px 30px}
.profile-top{display:flex;align-items:center;gap:18px;position:relative;z-index:1;flex-wrap:wrap}
.profile-avatar{width:64px;height:64px;border-radius:50%;background:linear-gradient(135deg,rgba(0,229,255,0.2),rgba(168,85,247,0.2));border:2px solid var(--accent);display:flex;align-items:center;justify-content:center;font-size:24px;color:var(--accent);font-family:'Outfit',sans-serif;font-weight:800}
.profile-name{font-family:'Outfit',sans-serif;font-size:22px;font-weight:800}
.profile-email{font-size:11px;color:var(--muted);margin-top:2px}
.profile-plan{display:inline-flex;align-items:center;gap:5px;margin-top:6px;padding:4px 12px;border-radius:20px;font-size:9px;font-weight:600}
.profile-plan.free{background:rgba(0,229,255,0.08);color:var(--accent);border:1px solid rgba(0,229,255,0.15)}
.profile-plan.pro{background:rgba(168,85,247,0.08);color:var(--purple);border:1px solid rgba(168,85,247,0.15)}
.profile-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:20px;position:relative;z-index:1}
.pstat{text-align:center;padding:14px;background:var(--glass);border:1px solid var(--border);border-radius:14px}
.pstat-num{font-family:'Outfit',sans-serif;font-size:24px;font-weight:800;color:var(--accent);line-height:1}
.pstat-lbl{font-size:8px;color:var(--muted);letter-spacing:0.12em;margin-top:4px}
.profile-tabs{display:flex;gap:4px;margin-bottom:16px}
.ptab{padding:8px 18px;border:1px solid var(--border);background:transparent;color:var(--muted);font-family:'Inter',sans-serif;font-size:10px;cursor:pointer;border-radius:8px;font-weight:500}
.ptab.on{background:rgba(0,229,255,0.06);border-color:var(--accent);color:var(--accent)}
.settings-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;max-width:500px}
.settings-grid .fg{margin-bottom:0}
.save-btn{padding:10px 24px;background:linear-gradient(135deg,var(--accent),#0099cc);color:#04080a;border:none;font-family:'Inter',sans-serif;font-size:11px;cursor:pointer;border-radius:10px;font-weight:600;margin-top:12px}
.danger-btn{padding:10px 24px;background:transparent;border:1px solid rgba(255,68,102,0.3);color:var(--red);font-family:'Inter',sans-serif;font-size:11px;cursor:pointer;border-radius:10px;margin-top:12px;margin-left:8px}
.usage-bar-container{margin-top:16px;margin-bottom:12px}
.usage-bar-bg{height:8px;background:var(--bg2);border-radius:4px;overflow:hidden}
.usage-bar{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));border-radius:4px;transition:width 0.5s}
.usage-text{display:flex;justify-content:space-between;margin-top:6px;font-size:10px;color:var(--muted)}
@media(max-width:768px){.detail-main{grid-template-columns:1fr}.detail-viewer{height:280px}.profile-stats{grid-template-columns:repeat(2,1fr)}.profile-top{text-align:center;justify-content:center;flex-direction:column}.settings-grid{grid-template-columns:1fr}.nav{padding:10px 14px}.container{padding:20px 12px}.style-grid{grid-template-columns:repeat(2,1fr)}.viewer{height:260px}.gal-grid,.blog-grid,.col-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<canvas id="bgCanvas"></canvas>
<div class="bg-gradient"></div>
<div class="bg-grid"></div>
<div class="scan-line"></div>

<!-- AUTH -->
<div class="auth-overlay" id="authOverlay">
  <div class="auth-box">
    <button class="auth-close" onclick="closeAuth()">&times;</button>
    <div class="auth-logo">PRINTFORGE</div>
    <div class="auth-tabs"><button class="auth-tab on" id="aLT" onclick="authTab('login')">Giris Yap</button><button class="auth-tab" id="aRT" onclick="authTab('register')">Kayit Ol</button></div>
    <div id="authMsg" class="auth-msg"></div>
    <div id="loginForm">
      <div class="fg"><label>E-POSTA</label><input type="email" id="lEmail" placeholder="ornek@gmail.com"></div>
      <div class="fg"><label>SIFRE</label><input type="password" id="lPass" placeholder="Sifreniz"></div>
      <button class="auth-btn" onclick="doLogin()">Giris Yap</button>
      <span class="auth-link" onclick="authTab('forgot')">Sifremi unuttum</span>
      <div class="auth-divider">veya</div>
      <button class="google-btn" onclick="googleLogin()"><svg viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.07 5.07 0 01-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/></svg>Google ile Devam Et</button>
      <div class="auth-footer">Hesabiniz yok mu? <a onclick="authTab('register')">Kayit Olun</a></div>
    </div>
    <div id="regForm" style="display:none">
      <div class="fg"><label>AD SOYAD</label><input type="text" id="rName" placeholder="Adiniz Soyadiniz"></div>
      <div class="fg"><label>E-POSTA</label><input type="email" id="rEmail" placeholder="ornek@gmail.com"></div>
      <div class="fg"><label>SIFRE</label><input type="password" id="rPass" placeholder="En az 6 karakter"></div>
      <button class="auth-btn" onclick="doRegister()">Kayit Ol</button>
      <div class="auth-divider">veya</div>
      <button class="google-btn" onclick="googleLogin()"><svg viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.07 5.07 0 01-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/></svg>Google ile Devam Et</button>
      <div class="auth-footer">Hesabiniz var mi? <a onclick="authTab('login')">Giris Yapin</a></div>
    </div>
    <div id="forgotForm" style="display:none">
      <p style="font-size:12px;color:var(--text);margin-bottom:16px">E-posta adresinizi girin, sifre sifirlama baglantisi gonderelim.</p>
      <div class="fg"><label>E-POSTA</label><input type="email" id="fEmail" placeholder="ornek@gmail.com"></div>
      <button class="auth-btn" onclick="doForgotPassword()">Sifirlama Maili Gonder</button>
      <div class="auth-footer" style="margin-top:16px"><a onclick="authTab('login')">Girise Don</a></div>
    </div>
    <div id="resetForm" style="display:none">
      <p style="font-size:12px;color:var(--text);margin-bottom:16px">Yeni sifrenizi belirleyin.</p>
      <div class="fg"><label>YENI SIFRE</label><input type="password" id="resetPass" placeholder="En az 6 karakter"></div>
      <div class="fg"><label>SIFRE TEKRAR</label><input type="password" id="resetPass2" placeholder="Ayni sifreyi girin"></div>
      <button class="auth-btn" onclick="doResetPassword()">Sifreyi Degistir</button>
    </div>
  </div>
</div>

<!-- DETAIL -->
<div class="detail-overlay" id="detailOverlay">
  <div class="detail-container">
    <button class="detail-back" onclick="closeDetail()">&#8592; Geri Don</button>
    <div class="detail-main">
      <div class="detail-viewer" id="detailViewer"></div>
      <div class="detail-info">
        <h2 class="detail-title" id="dTitle">-</h2>
        <div class="detail-author"><div class="detail-author-avatar" id="dAvatar">U</div><span id="dAuthor">-</span></div>
        <div class="detail-stats-row">
          <div class="detail-stat"><div class="detail-stat-num" id="dLikes">0</div><div class="detail-stat-lbl">BEGENI</div></div>
          <div class="detail-stat"><div class="detail-stat-num" id="dDls">0</div><div class="detail-stat-lbl">INDIRME</div></div>
          <div class="detail-stat"><div class="detail-stat-num" id="dType">-</div><div class="detail-stat-lbl">TUR</div></div>
        </div>
        <div class="detail-section" id="dPromptSec"><div class="detail-section-title">KULLANILAN PROMPT</div><div class="detail-prompt" id="dPrompt">-</div></div>
        <div class="detail-section"><div class="detail-section-title">ETIKETLER</div><div class="detail-tags" id="dTags"></div></div>
        <div class="detail-section"><div class="detail-section-title">INDIR</div><div class="detail-dl-grid" id="dDlGrid"></div></div>
        <button class="detail-like-btn" id="dLikeBtn" onclick="likeDetail()">&#9829; Begen</button>
        <button class="col-add-btn" onclick="toggleColDropdown()">+ Koleksiyona Ekle</button>
        <div class="col-dropdown" id="colDropdown"><div id="colDropdownList"></div>
          <div class="col-new-input"><input type="text" id="newColName" placeholder="Yeni koleksiyon adi"><button onclick="createColAndAdd()">Olustur</button></div>
        </div>
      </div>
    </div>
    <!-- YORUMLAR -->
    <div class="comments-section">
      <div class="comments-title">Yorumlar (<span id="commentCount">0</span>)</div>
      <div class="comment-input"><textarea id="commentText" placeholder="Yorum yazin..." rows="2"></textarea><button class="comment-send" onclick="addComment()">Gonder</button></div>
      <div class="comment-list" id="commentList"></div>
    </div>
    <div class="similar-section" id="simSec"><div class="similar-title">Benzer Modeller</div><div class="similar-grid" id="simGrid"></div></div>
  </div>
</div>
<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PrintForge - AI ile 3D Model Uretici</title>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800;900&family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<script type="module" src="https://unpkg.com/@google/model-viewer@3.5.0/dist/model-viewer.min.js"></script>
<style>
:root{--bg:#04080a;--bg2:#070d10;--border:#0e2028;--accent:#00e5ff;--accent2:#00ff9d;--text:#c8dde5;--muted:#2a4a5a;--card:#060c10;--red:#ff4466;--orange:#ffaa00;--purple:#a855f7;--glass:rgba(6,12,16,0.85)}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;-webkit-font-smoothing:antialiased;overflow-x:hidden}
h1,h2,h3,h4{font-family:'Outfit',sans-serif}
::-webkit-scrollbar{width:4px}::-webkit-scrollbar-thumb{background:var(--muted);border-radius:4px}
#bgCanvas{position:fixed;inset:0;z-index:0;pointer-events:none}
.bg-gradient{position:fixed;inset:0;z-index:0;pointer-events:none;background:radial-gradient(ellipse 60% 50% at 20% 20%,rgba(0,229,255,0.04),transparent),radial-gradient(ellipse 50% 40% at 80% 80%,rgba(0,255,157,0.03),transparent)}
.bg-grid{position:fixed;inset:0;z-index:0;pointer-events:none;background-image:linear-gradient(rgba(0,229,255,0.015) 1px,transparent 1px),linear-gradient(90deg,rgba(0,229,255,0.015) 1px,transparent 1px);background-size:60px 60px}
.scan-line{position:fixed;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(0,229,255,0.04),transparent);animation:scanLine 8s linear infinite;z-index:0;pointer-events:none}
@keyframes scanLine{from{top:-1px}to{top:100vh}}
.nav{position:sticky;top:0;z-index:100;display:flex;justify-content:space-between;align-items:center;padding:12px 24px;background:var(--glass);backdrop-filter:blur(24px);border-bottom:1px solid rgba(0,229,255,0.06);gap:10px;flex-wrap:wrap}
.nav-logo{display:flex;align-items:center;gap:8px;text-decoration:none}
.nlm{width:22px;height:22px;border:1.5px solid var(--accent);transform:rotate(45deg);display:flex;align-items:center;justify-content:center;transition:all 0.3s}
.nav-logo:hover .nlm{border-color:var(--accent2)}
.nli{width:6px;height:6px;background:var(--accent);transform:rotate(-45deg)}
.nlt{font-family:'Outfit',sans-serif;font-size:15px;font-weight:800;color:var(--accent);letter-spacing:0.08em}
.nav-right{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.nav-status{font-size:8px;color:var(--muted);display:flex;align-items:center;gap:4px;padding:4px 10px;border:1px solid var(--border);border-radius:20px}
.nav-dot{width:5px;height:5px;border-radius:50%;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
.nav-user{display:flex;align-items:center;gap:8px}
.nav-avatar{width:30px;height:30px;border-radius:50%;background:linear-gradient(135deg,rgba(0,229,255,0.2),rgba(168,85,247,0.2));border:1.5px solid var(--accent);display:flex;align-items:center;justify-content:center;font-size:12px;color:var(--accent);cursor:pointer;font-family:'Outfit',sans-serif;font-weight:700}
.nav-uname{font-size:10px;color:var(--text);font-weight:500}
.nav-usage{font-size:8px;color:var(--accent2);background:rgba(0,255,157,0.06);padding:3px 10px;border:1px solid rgba(0,255,157,0.15);border-radius:20px;font-weight:600}
.nbtn{padding:6px 14px;font-family:'Inter',sans-serif;font-size:9px;letter-spacing:0.08em;cursor:pointer;transition:all 0.2s;border:1px solid var(--border);background:transparent;color:var(--text);border-radius:8px;font-weight:500}
.nbtn:hover{border-color:var(--accent);color:var(--accent)}
.nbtn.accent{background:linear-gradient(135deg,var(--accent),#0099cc);color:#04080a;border:none;font-weight:600}
.nbtn.accent:hover{background:linear-gradient(135deg,var(--accent2),var(--accent))}
.nbtn.red{color:var(--red);border-color:rgba(255,68,102,0.2)}
.banner{padding:8px 20px;text-align:center;font-size:9px;display:none;position:relative;z-index:1}
.banner.demo{background:rgba(255,170,0,0.06);color:var(--orange);border-bottom:1px solid rgba(255,170,0,0.1)}
.banner.usage{background:rgba(0,255,157,0.04);color:var(--accent2);border-bottom:1px solid rgba(0,255,157,0.1)}
.banner.verify{background:rgba(255,170,0,0.06);color:var(--orange);border-bottom:1px solid rgba(255,170,0,0.1)}
.banner a{color:var(--accent);cursor:pointer;text-decoration:underline}
.container{position:relative;z-index:1;max-width:920px;margin:0 auto;padding:28px 20px 80px}

/* AUTH */
.auth-overlay{display:none;position:fixed;inset:0;z-index:200;background:rgba(4,8,10,0.94);backdrop-filter:blur(8px);align-items:center;justify-content:center;padding:20px}
.auth-overlay.on{display:flex}
.auth-box{background:var(--card);border:1px solid var(--border);padding:36px 30px;width:100%;max-width:400px;position:relative;border-radius:20px;box-shadow:0 24px 64px rgba(0,0,0,0.4)}
.auth-close{position:absolute;top:14px;right:16px;background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer}
.auth-logo{text-align:center;margin-bottom:20px;font-family:'Outfit',sans-serif;font-size:20px;font-weight:800;color:var(--accent)}
.auth-tabs{display:flex;border:1px solid var(--border);margin-bottom:18px;border-radius:10px;overflow:hidden}
.auth-tab{flex:1;padding:10px;background:transparent;border:none;color:var(--muted);font-family:'Inter',sans-serif;font-size:10px;cursor:pointer;font-weight:500}
.auth-tab.on{background:rgba(0,229,255,0.06);color:var(--accent)}
.fg{margin-bottom:12px}
.fg label{font-size:9px;letter-spacing:0.1em;color:var(--muted);margin-bottom:5px;display:block;font-weight:500}
.fg input{width:100%;background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:11px 14px;font-size:13px;font-family:'Inter',sans-serif;border-radius:10px}
.fg input:focus{outline:none;border-color:rgba(0,229,255,0.4)}
.fg input::placeholder{color:var(--muted)}
.auth-btn{width:100%;padding:13px;background:linear-gradient(135deg,var(--accent),#0099cc);color:#04080a;border:none;font-family:'Inter',sans-serif;font-size:12px;cursor:pointer;margin-top:8px;border-radius:10px;font-weight:700}
.auth-btn:hover{background:linear-gradient(135deg,var(--accent2),var(--accent))}
.auth-divider{display:flex;align-items:center;gap:12px;margin:16px 0;color:var(--muted);font-size:9px}
.auth-divider::before,.auth-divider::after{content:'';flex:1;height:1px;background:var(--border)}
.google-btn{width:100%;padding:11px;background:transparent;border:1px solid var(--border);color:var(--text);font-family:'Inter',sans-serif;font-size:11px;cursor:pointer;border-radius:10px;display:flex;align-items:center;justify-content:center;gap:10px;font-weight:500}
.google-btn:hover{border-color:var(--accent)}
.google-btn svg{width:16px;height:16px}
.auth-msg{padding:8px 12px;font-size:10px;margin-bottom:10px;display:none;border-radius:8px;line-height:1.6}
.auth-msg.err{background:rgba(255,68,102,0.08);border:1px solid rgba(255,68,102,0.2);color:var(--red);display:block}
.auth-msg.ok{background:rgba(0,255,157,0.08);border:1px solid rgba(0,255,157,0.2);color:var(--accent2);display:block}
.auth-footer{text-align:center;margin-top:14px;font-size:9px;color:var(--muted)}
.auth-footer a{color:var(--accent);cursor:pointer}
.auth-link{font-size:10px;color:var(--accent);cursor:pointer;text-align:center;display:block;margin-top:10px}

/* TABS */
.tabs{display:flex;border:1px solid var(--border);margin-bottom:24px;border-radius:12px;overflow:hidden;background:var(--glass)}
.tab{flex:1;padding:12px 6px;background:transparent;border:none;color:var(--muted);font-family:'Inter',sans-serif;font-size:9px;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:4px;font-weight:500;position:relative}
.tab.on{color:var(--accent)}
.tab.on::after{content:'';position:absolute;bottom:0;left:15%;right:15%;height:2px;background:var(--accent);border-radius:1px}
.tab:hover:not(.on){background:rgba(0,229,255,0.02);color:var(--text)}
.panel{display:none}.panel.on{display:block}
.card{background:var(--glass);border:1px solid var(--border);padding:26px;margin-bottom:14px;border-radius:16px}
.label{font-size:9px;letter-spacing:0.12em;color:var(--muted);margin-bottom:6px;display:block;font-weight:600}
textarea{width:100%;background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:12px;font-size:13px;font-family:'Inter',sans-serif;resize:vertical;min-height:70px;border-radius:12px}
textarea:focus{outline:none;border-color:rgba(0,229,255,0.4)}
textarea::placeholder{color:var(--muted)}
.examples{margin-top:10px;display:flex;gap:5px;flex-wrap:wrap}
.ex-btn{padding:6px 12px;border:1px solid var(--border);background:transparent;color:var(--muted);font-family:'Inter',sans-serif;font-size:9px;cursor:pointer;border-radius:8px;font-weight:500}
.ex-btn:hover{border-color:var(--accent);color:var(--accent)}
.style-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:6px}
.style-opt{padding:12px 8px;border:1px solid var(--border);background:transparent;color:var(--muted);font-family:'Inter',sans-serif;font-size:9px;cursor:pointer;text-align:center;border-radius:12px;font-weight:500}
.style-opt:hover{border-color:rgba(0,229,255,0.3);color:var(--text)}
.style-opt.on{border-color:var(--accent);color:var(--accent);background:rgba(0,229,255,0.04)}
.style-opt .ico{font-size:18px;display:block;margin-bottom:4px}
.upload{border:2px dashed var(--border);padding:36px 20px;text-align:center;cursor:pointer;position:relative;overflow:hidden;border-radius:16px}
.upload:hover,.upload.drag{border-color:var(--accent)}
.upload.has{border-color:var(--accent2);border-style:solid}
.upload input{position:absolute;inset:0;opacity:0;cursor:pointer}
.upload .ico{font-size:30px;margin-bottom:8px;color:var(--accent)}
.upload p{font-size:11px;color:var(--muted)}
.preview{margin-top:14px;display:none;position:relative}.preview.on{display:block}
.preview img{max-width:100%;max-height:200px;display:block;margin:0 auto;border:1px solid var(--border);border-radius:12px}
.preview .rm{position:absolute;top:6px;right:6px;width:26px;height:26px;background:rgba(255,68,102,0.85);border:none;color:#fff;border-radius:50%;cursor:pointer;font-size:11px}
.gen-btn{width:100%;padding:14px;background:linear-gradient(135deg,var(--accent),#0099cc);color:#04080a;border:none;font-family:'Inter',sans-serif;font-size:12px;cursor:pointer;font-weight:700;margin-top:14px;border-radius:12px}
.gen-btn:hover:not(:disabled){background:linear-gradient(135deg,var(--accent2),var(--accent))}
.gen-btn:disabled{opacity:0.4;cursor:not-allowed}
.sec{display:none;margin-bottom:20px}.sec.on{display:block}
.prog-card{background:var(--glass);border:1px solid var(--border);padding:24px;border-radius:16px}
.prog-top{display:flex;justify-content:space-between;margin-bottom:14px}
.prog-title{font-family:'Outfit',sans-serif;font-size:15px;font-weight:700}
.prog-pct{font-family:'Outfit',sans-serif;font-size:22px;font-weight:800;color:var(--accent)}
.prog-bar-bg{width:100%;height:6px;background:var(--bg2);overflow:hidden;margin-bottom:10px;border-radius:3px}
.prog-bar{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));width:0%;transition:width 0.5s;border-radius:3px}
.prog-step{font-size:10px;color:var(--muted);display:flex;align-items:center;gap:6px}
.spinner{display:inline-block;width:10px;height:10px;border:2px solid var(--muted);border-top-color:var(--accent);border-radius:50%;animation:spin 0.8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.result-card{background:var(--glass);border:1px solid var(--accent2);padding:24px;text-align:center;border-radius:16px}
.result-card h3{font-family:'Outfit',sans-serif;font-size:20px;font-weight:800;margin-bottom:6px}
.result-card>p{font-size:11px;color:var(--muted);margin-bottom:16px}
.viewer{width:100%;height:360px;background:var(--bg2);border:1px solid var(--border);margin-bottom:16px;overflow:hidden;display:flex;align-items:center;justify-content:center;border-radius:14px}
.viewer model-viewer{width:100%;height:100%}
.dl-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:10px}
.dl-btn{padding:12px 8px;border:1px solid var(--border);background:var(--card);color:var(--text);font-family:'Inter',sans-serif;font-size:10px;cursor:pointer;text-decoration:none;text-align:center;display:flex;flex-direction:column;align-items:center;gap:3px;border-radius:10px;font-weight:500}
.dl-btn:hover{border-color:var(--accent);color:var(--accent)}
.dl-btn .dl-fmt{font-size:7px;color:var(--muted)}
.dl-btn.primary{border-color:var(--accent2);background:rgba(0,255,157,0.05)}
.new-btn{width:100%;padding:11px;border:1px solid var(--border);background:transparent;color:var(--text);font-family:'Inter',sans-serif;font-size:10px;cursor:pointer;border-radius:10px;font-weight:500}
.new-btn:hover{border-color:var(--accent);color:var(--accent)}
.err-card{background:rgba(255,68,102,0.04);border:1px solid rgba(255,68,102,0.15);padding:24px;text-align:center;border-radius:16px}
.err-card h3{color:var(--red);font-size:15px;margin-bottom:6px}
.err-card p{font-size:10px;color:var(--muted);margin-bottom:14px}

/* GALLERY */
.gal-toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:16px}
.gal-toolbar input{flex:1;min-width:140px;background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:10px 14px;font-family:'Inter',sans-serif;font-size:12px;border-radius:10px}
.gal-toolbar select{background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:10px;font-family:'Inter',sans-serif;font-size:11px;border-radius:10px}
.gal-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px}
.gal-card{background:var(--card);border:1px solid var(--border);cursor:pointer;border-radius:16px;overflow:hidden;transition:all 0.25s}
.gal-card:hover{border-color:rgba(0,229,255,0.25);transform:translateY(-4px);box-shadow:0 16px 40px rgba(0,0,0,0.3)}
.gal-thumb{height:180px;background:var(--bg2);overflow:hidden;display:flex;align-items:center;justify-content:center;position:relative}
.gal-thumb model-viewer{width:100%;height:100%}
.gal-badge{position:absolute;top:8px;left:8px;background:var(--glass);border:1px solid rgba(0,229,255,0.15);padding:3px 8px;font-size:8px;color:var(--accent);border-radius:6px;font-weight:600}
.gal-body{padding:14px}
.gal-title{font-family:'Outfit',sans-serif;font-size:14px;font-weight:700;margin-bottom:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gal-meta{font-size:9px;color:var(--muted);display:flex;justify-content:space-between;margin-bottom:8px}
.gal-stats{display:flex;gap:14px;margin-bottom:10px}
.gal-stat{font-size:9px;color:var(--muted)}.gal-stat span{color:var(--accent2);font-weight:600}
.gal-actions{display:flex;gap:5px}
.gal-btn{flex:1;padding:7px;border:1px solid var(--border);background:transparent;color:var(--text);font-family:'Inter',sans-serif;font-size:9px;cursor:pointer;text-align:center;border-radius:8px;text-decoration:none;display:flex;align-items:center;justify-content:center;font-weight:500}
.gal-btn:hover{border-color:var(--accent);color:var(--accent)}
.gal-btn.liked{color:var(--red);border-color:rgba(255,68,102,0.3)}
.gal-btn.dl{background:rgba(0,229,255,0.04);border-color:rgba(0,229,255,0.15)}
.gal-empty{text-align:center;padding:60px 20px;color:var(--muted);font-size:12px;grid-column:1/-1}

/* DETAIL */
.detail-overlay{display:none;position:fixed;inset:0;z-index:150;background:rgba(4,8,10,0.96);overflow-y:auto;padding:20px}
.detail-overlay.on{display:block}
.detail-container{max-width:900px;margin:0 auto}
.detail-back{display:inline-flex;align-items:center;gap:6px;color:var(--muted);font-size:11px;cursor:pointer;margin-bottom:20px;padding:8px 16px;border:1px solid var(--border);background:transparent;border-radius:10px;font-family:'Inter',sans-serif;font-weight:500}
.detail-back:hover{border-color:var(--accent);color:var(--accent)}
.detail-main{display:grid;grid-template-columns:1.3fr 1fr;gap:24px;margin-bottom:24px}
.detail-viewer{background:var(--bg2);border:1px solid var(--border);border-radius:16px;overflow:hidden;height:420px}
.detail-viewer model-viewer{width:100%;height:100%}
.detail-info{display:flex;flex-direction:column}
.detail-title{font-family:'Outfit',sans-serif;font-size:24px;font-weight:800;margin-bottom:8px}
.detail-author{font-size:12px;color:var(--muted);margin-bottom:16px;display:flex;align-items:center;gap:8px}
.detail-author-avatar{width:26px;height:26px;border-radius:50%;background:linear-gradient(135deg,rgba(0,229,255,0.2),rgba(168,85,247,0.2));border:1px solid var(--accent);display:flex;align-items:center;justify-content:center;font-size:10px;color:var(--accent);font-weight:700}
.detail-stats-row{display:flex;gap:16px;margin-bottom:18px;padding:14px;background:var(--bg2);border-radius:12px}
.detail-stat{text-align:center;flex:1}
.detail-stat-num{font-family:'Outfit',sans-serif;font-size:20px;font-weight:800;color:var(--accent)}
.detail-stat-lbl{font-size:7px;color:var(--muted);letter-spacing:0.12em;margin-top:2px}
.detail-section{margin-bottom:14px}
.detail-section-title{font-size:9px;letter-spacing:0.12em;color:var(--muted);margin-bottom:8px;font-weight:600}
.detail-tags{display:flex;gap:6px;flex-wrap:wrap}
.detail-tag{padding:4px 10px;background:rgba(0,229,255,0.04);border:1px solid rgba(0,229,255,0.12);color:var(--accent);font-size:9px;border-radius:8px;font-weight:500}
.detail-prompt{background:var(--bg2);border:1px solid var(--border);padding:12px 16px;font-size:12px;color:var(--text);line-height:1.7;border-radius:12px;font-style:italic}
.detail-dl-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:8px}
.detail-dl{padding:14px;border:1px solid var(--border);background:var(--card);text-align:center;cursor:pointer;text-decoration:none;color:var(--text);border-radius:12px;font-weight:500}
.detail-dl:hover{border-color:var(--accent);color:var(--accent)}
.detail-dl .dl-name{font-size:12px;font-weight:600}
.detail-dl .dl-desc{font-size:8px;color:var(--muted);margin-top:2px}
.detail-dl.primary{border-color:var(--accent2);background:rgba(0,255,157,0.04)}
.detail-like-btn{width:100%;padding:12px;border:1px solid var(--border);background:transparent;color:var(--text);font-family:'Inter',sans-serif;font-size:12px;cursor:pointer;margin-top:10px;border-radius:12px;display:flex;align-items:center;justify-content:center;gap:8px;font-weight:600}
.detail-like-btn:hover{border-color:var(--red);color:var(--red)}
.detail-like-btn.liked{background:rgba(255,68,102,0.06);border-color:var(--red);color:var(--red)}

/* COMMENTS */
.comments-section{margin-bottom:24px}
.comments-title{font-family:'Outfit',sans-serif;font-size:16px;font-weight:700;margin-bottom:14px;display:flex;align-items:center;gap:8px}
.comments-title::before{content:'';width:3px;height:16px;background:var(--accent);border-radius:2px}
.comment-input{display:flex;gap:8px;margin-bottom:16px}
.comment-input textarea{min-height:40px;flex:1;border-radius:10px;font-size:12px;padding:10px}
.comment-send{padding:10px 20px;background:var(--accent);color:#04080a;border:none;border-radius:10px;font-family:'Inter',sans-serif;font-size:11px;cursor:pointer;font-weight:600;align-self:flex-end}
.comment-send:hover{background:var(--accent2)}
.comment-list{display:flex;flex-direction:column;gap:10px}
.comment-item{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:14px}
.comment-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.comment-author{font-size:11px;font-weight:600;color:var(--accent)}
.comment-date{font-size:9px;color:var(--muted)}
.comment-text{font-size:12px;line-height:1.6;color:var(--text)}
.comment-delete{background:none;border:none;color:var(--red);font-size:9px;cursor:pointer;margin-top:6px}

/* COLLECTION ADD */
.col-add-btn{padding:8px 14px;border:1px solid var(--border);background:transparent;color:var(--muted);font-family:'Inter',sans-serif;font-size:9px;cursor:pointer;border-radius:8px;margin-top:8px;width:100%;text-align:center}
.col-add-btn:hover{border-color:var(--accent);color:var(--accent)}
.col-dropdown{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:10px;margin-top:8px;display:none}
.col-dropdown.on{display:block}
.col-dropdown-item{padding:8px 12px;cursor:pointer;font-size:11px;border-radius:6px;display:flex;justify-content:space-between}
.col-dropdown-item:hover{background:rgba(0,229,255,0.06);color:var(--accent)}
.col-new-input{display:flex;gap:6px;margin-top:8px}
.col-new-input input{flex:1;background:var(--card);border:1px solid var(--border);color:var(--text);padding:8px 12px;font-size:11px;font-family:'Inter',sans-serif;border-radius:8px}
.col-new-input button{padding:8px 14px;background:var(--accent);color:#04080a;border:none;border-radius:8px;font-size:10px;cursor:pointer;font-weight:600}

/* SIMILAR */
.similar-section{margin-bottom:40px}
.similar-title{font-family:'Outfit',sans-serif;font-size:18px;font-weight:700;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.similar-title::before{content:'';width:3px;height:18px;background:linear-gradient(var(--accent),var(--accent2));border-radius:2px}
.similar-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
.similar-card{background:var(--card);border:1px solid var(--border);border-radius:14px;overflow:hidden;cursor:pointer}
.similar-card:hover{border-color:rgba(0,229,255,0.25);transform:translateY(-2px)}
.similar-thumb{height:130px;background:var(--bg2);overflow:hidden}
.similar-thumb model-viewer{width:100%;height:100%}
.similar-body{padding:10px 12px}
.similar-name{font-family:'Outfit',sans-serif;font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.similar-meta{font-size:8px;color:var(--muted);margin-top:3px}

/* COLLECTIONS */
.col-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;margin-top:16px}
.col-card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:20px;cursor:pointer;transition:all 0.2s}
.col-card:hover{border-color:rgba(0,229,255,0.25);transform:translateY(-2px)}
.col-card-name{font-family:'Outfit',sans-serif;font-size:16px;font-weight:700;margin-bottom:4px}
.col-card-desc{font-size:10px;color:var(--muted);margin-bottom:10px;line-height:1.5}
.col-card-meta{font-size:9px;color:var(--muted);display:flex;justify-content:space-between}
.col-card-count{color:var(--accent);font-weight:600}
.col-create-card{background:transparent;border:2px dashed var(--border);border-radius:16px;padding:30px 20px;cursor:pointer;text-align:center;transition:all 0.2s}
.col-create-card:hover{border-color:var(--accent)}
.col-create-card .ico{font-size:28px;color:var(--accent);margin-bottom:8px}
.col-create-card p{font-size:11px;color:var(--muted)}
.col-detail-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:10px}
.col-detail-title{font-family:'Outfit',sans-serif;font-size:20px;font-weight:800}
.col-detail-desc{font-size:11px;color:var(--muted);margin-bottom:16px}

/* BLOG */
.blog-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;margin-top:16px}
.blog-card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:24px;cursor:pointer;transition:all 0.2s}
.blog-card:hover{border-color:rgba(0,229,255,0.25);transform:translateY(-2px)}
.blog-cat{font-size:8px;color:var(--accent);letter-spacing:0.12em;font-weight:600;margin-bottom:8px;text-transform:uppercase}
.blog-card-title{font-family:'Outfit',sans-serif;font-size:16px;font-weight:700;margin-bottom:8px;line-height:1.3}
.blog-card-summary{font-size:11px;color:var(--muted);line-height:1.6;margin-bottom:12px}
.blog-card-meta{font-size:9px;color:var(--muted);display:flex;gap:16px}
.blog-detail{max-width:700px;margin:0 auto}
.blog-detail-title{font-family:'Outfit',sans-serif;font-size:28px;font-weight:800;margin-bottom:12px;line-height:1.2}
.blog-detail-meta{font-size:10px;color:var(--muted);margin-bottom:24px;display:flex;gap:16px}
.blog-detail-content{font-size:14px;line-height:1.9;color:var(--text)}
.blog-detail-content h2{font-family:'Outfit',sans-serif;font-size:20px;font-weight:700;color:var(--accent);margin:28px 0 12px}
.blog-detail-content h3{font-family:'Outfit',sans-serif;font-size:16px;font-weight:600;margin:20px 0 8px}
.blog-detail-content p{margin-bottom:14px}
.blog-detail-content strong{color:var(--accent2)}
.blog-back{display:inline-flex;align-items:center;gap:6px;color:var(--muted);font-size:11px;cursor:pointer;margin-bottom:24px;padding:8px 16px;border:1px solid var(--border);background:transparent;border-radius:10px;font-weight:500}
.blog-back:hover{border-color:var(--accent);color:var(--accent)}

/* PROFILE */
.profile-header{background:linear-gradient(135deg,rgba(0,229,255,0.06),rgba(168,85,247,0.04));border:1px solid var(--border);border-radius:20px;padding:30px;margin-bottom:20px;position:relative;overflow:hidden}
.profile-header::before{content:'';position:absolute;inset:0;background-image:linear-gradient(rgba(0,229,255,0.03) 1px,transparent 1px),linear-gradient(90deg,rgba(0,229,255,0.03) 1px,transparent 1px);background-size:30px 30px}
.profile-top{display:flex;align-items:center;gap:18px;position:relative;z-index:1;flex-wrap:wrap}
.profile-avatar{width:64px;height:64px;border-radius:50%;background:linear-gradient(135deg,rgba(0,229,255,0.2),rgba(168,85,247,0.2));border:2px solid var(--accent);display:flex;align-items:center;justify-content:center;font-size:24px;color:var(--accent);font-family:'Outfit',sans-serif;font-weight:800}
.profile-name{font-family:'Outfit',sans-serif;font-size:22px;font-weight:800}
.profile-email{font-size:11px;color:var(--muted);margin-top:2px}
.profile-plan{display:inline-flex;align-items:center;gap:5px;margin-top:6px;padding:4px 12px;border-radius:20px;font-size:9px;font-weight:600}
.profile-plan.free{background:rgba(0,229,255,0.08);color:var(--accent);border:1px solid rgba(0,229,255,0.15)}
.profile-plan.pro{background:rgba(168,85,247,0.08);color:var(--purple);border:1px solid rgba(168,85,247,0.15)}
.profile-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:20px;position:relative;z-index:1}
.pstat{text-align:center;padding:14px;background:var(--glass);border:1px solid var(--border);border-radius:14px}
.pstat-num{font-family:'Outfit',sans-serif;font-size:24px;font-weight:800;color:var(--accent);line-height:1}
.pstat-lbl{font-size:8px;color:var(--muted);letter-spacing:0.12em;margin-top:4px}
.profile-tabs{display:flex;gap:4px;margin-bottom:16px}
.ptab{padding:8px 18px;border:1px solid var(--border);background:transparent;color:var(--muted);font-family:'Inter',sans-serif;font-size:10px;cursor:pointer;border-radius:8px;font-weight:500}
.ptab.on{background:rgba(0,229,255,0.06);border-color:var(--accent);color:var(--accent)}
.settings-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;max-width:500px}
.settings-grid .fg{margin-bottom:0}
.save-btn{padding:10px 24px;background:linear-gradient(135deg,var(--accent),#0099cc);color:#04080a;border:none;font-family:'Inter',sans-serif;font-size:11px;cursor:pointer;border-radius:10px;font-weight:600;margin-top:12px}
.danger-btn{padding:10px 24px;background:transparent;border:1px solid rgba(255,68,102,0.3);color:var(--red);font-family:'Inter',sans-serif;font-size:11px;cursor:pointer;border-radius:10px;margin-top:12px;margin-left:8px}
.usage-bar-container{margin-top:16px;margin-bottom:12px}
.usage-bar-bg{height:8px;background:var(--bg2);border-radius:4px;overflow:hidden}
.usage-bar{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));border-radius:4px;transition:width 0.5s}
.usage-text{display:flex;justify-content:space-between;margin-top:6px;font-size:10px;color:var(--muted)}
@media(max-width:768px){.detail-main{grid-template-columns:1fr}.detail-viewer{height:280px}.profile-stats{grid-template-columns:repeat(2,1fr)}.profile-top{text-align:center;justify-content:center;flex-direction:column}.settings-grid{grid-template-columns:1fr}.nav{padding:10px 14px}.container{padding:20px 12px}.style-grid{grid-template-columns:repeat(2,1fr)}.viewer{height:260px}.gal-grid,.blog-grid,.col-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<canvas id="bgCanvas"></canvas>
<div class="bg-gradient"></div>
<div class="bg-grid"></div>
<div class="scan-line"></div>

<!-- AUTH -->
<div class="auth-overlay" id="authOverlay">
  <div class="auth-box">
    <button class="auth-close" onclick="closeAuth()">&times;</button>
    <div class="auth-logo">PRINTFORGE</div>
    <div class="auth-tabs"><button class="auth-tab on" id="aLT" onclick="authTab('login')">Giris Yap</button><button class="auth-tab" id="aRT" onclick="authTab('register')">Kayit Ol</button></div>
    <div id="authMsg" class="auth-msg"></div>
    <div id="loginForm">
      <div class="fg"><label>E-POSTA</label><input type="email" id="lEmail" placeholder="ornek@gmail.com"></div>
      <div class="fg"><label>SIFRE</label><input type="password" id="lPass" placeholder="Sifreniz"></div>
      <button class="auth-btn" onclick="doLogin()">Giris Yap</button>
      <span class="auth-link" onclick="authTab('forgot')">Sifremi unuttum</span>
      <div class="auth-divider">veya</div>
      <button class="google-btn" onclick="googleLogin()"><svg viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.07 5.07 0 01-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/></svg>Google ile Devam Et</button>
      <div class="auth-footer">Hesabiniz yok mu? <a onclick="authTab('register')">Kayit Olun</a></div>
    </div>
    <div id="regForm" style="display:none">
      <div class="fg"><label>AD SOYAD</label><input type="text" id="rName" placeholder="Adiniz Soyadiniz"></div>
      <div class="fg"><label>E-POSTA</label><input type="email" id="rEmail" placeholder="ornek@gmail.com"></div>
      <div class="fg"><label>SIFRE</label><input type="password" id="rPass" placeholder="En az 6 karakter"></div>
      <button class="auth-btn" onclick="doRegister()">Kayit Ol</button>
      <div class="auth-divider">veya</div>
      <button class="google-btn" onclick="googleLogin()"><svg viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.07 5.07 0 01-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/></svg>Google ile Devam Et</button>
      <div class="auth-footer">Hesabiniz var mi? <a onclick="authTab('login')">Giris Yapin</a></div>
    </div>
    <div id="forgotForm" style="display:none">
      <p style="font-size:12px;color:var(--text);margin-bottom:16px">E-posta adresinizi girin, sifre sifirlama baglantisi gonderelim.</p>
      <div class="fg"><label>E-POSTA</label><input type="email" id="fEmail" placeholder="ornek@gmail.com"></div>
      <button class="auth-btn" onclick="doForgotPassword()">Sifirlama Maili Gonder</button>
      <div class="auth-footer" style="margin-top:16px"><a onclick="authTab('login')">Girise Don</a></div>
    </div>
    <div id="resetForm" style="display:none">
      <p style="font-size:12px;color:var(--text);margin-bottom:16px">Yeni sifrenizi belirleyin.</p>
      <div class="fg"><label>YENI SIFRE</label><input type="password" id="resetPass" placeholder="En az 6 karakter"></div>
      <div class="fg"><label>SIFRE TEKRAR</label><input type="password" id="resetPass2" placeholder="Ayni sifreyi girin"></div>
      <button class="auth-btn" onclick="doResetPassword()">Sifreyi Degistir</button>
    </div>
  </div>
</div>

<!-- DETAIL -->
<div class="detail-overlay" id="detailOverlay">
  <div class="detail-container">
    <button class="detail-back" onclick="closeDetail()">&#8592; Geri Don</button>
    <div class="detail-main">
      <div class="detail-viewer" id="detailViewer"></div>
      <div class="detail-info">
        <h2 class="detail-title" id="dTitle">-</h2>
        <div class="detail-author"><div class="detail-author-avatar" id="dAvatar">U</div><span id="dAuthor">-</span></div>
        <div class="detail-stats-row">
          <div class="detail-stat"><div class="detail-stat-num" id="dLikes">0</div><div class="detail-stat-lbl">BEGENI</div></div>
          <div class="detail-stat"><div class="detail-stat-num" id="dDls">0</div><div class="detail-stat-lbl">INDIRME</div></div>
          <div class="detail-stat"><div class="detail-stat-num" id="dType">-</div><div class="detail-stat-lbl">TUR</div></div>
        </div>
        <div class="detail-section" id="dPromptSec"><div class="detail-section-title">KULLANILAN PROMPT</div><div class="detail-prompt" id="dPrompt">-</div></div>
        <div class="detail-section"><div class="detail-section-title">ETIKETLER</div><div class="detail-tags" id="dTags"></div></div>
        <div class="detail-section"><div class="detail-section-title">INDIR</div><div class="detail-dl-grid" id="dDlGrid"></div></div>
        <button class="detail-like-btn" id="dLikeBtn" onclick="likeDetail()">&#9829; Begen</button>
        <button class="col-add-btn" onclick="toggleColDropdown()">+ Koleksiyona Ekle</button>
        <div class="col-dropdown" id="colDropdown"><div id="colDropdownList"></div>
          <div class="col-new-input"><input type="text" id="newColName" placeholder="Yeni koleksiyon adi"><button onclick="createColAndAdd()">Olustur</button></div>
        </div>
      </div>
    </div>
    <!-- YORUMLAR -->
    <div class="comments-section">
      <div class="comments-title">Yorumlar (<span id="commentCount">0</span>)</div>
      <div class="comment-input"><textarea id="commentText" placeholder="Yorum yazin..." rows="2"></textarea><button class="comment-send" onclick="addComment()">Gonder</button></div>
      <div class="comment-list" id="commentList"></div>
    </div>
    <div class="similar-section" id="simSec"><div class="similar-title">Benzer Modeller</div><div class="similar-grid" id="simGrid"></div></div>
  </div>
</div>
