/**
 * Tracking Dashboard — STANDALONE Google Apps Script
 *
 * Paste this into a NEW Google Sheet (Extensions > Apps Script).
 * This sheet talks ONLY to Azure — it doesn't need access to any
 * campaign sheets. All tracking data comes from Azure Cosmos DB.
 *
 * SETUP:
 * 1. Create a new Google Sheet (drive.google.com → New → Google Sheets)
 * 2. Extensions → Apps Script
 * 3. Paste this entire file
 * 4. Change TRACKING_SYNC_URL and TRACKING_API_KEY below
 * 5. Run onOpen() once to create the menu
 * 6. Click "📊 Sync Dashboard" to populate
 */

// ═══════════════════════════════════════════════════════════════════
// CONFIG — CHANGE THESE
// ═══════════════════════════════════════════════════════════════════

var TRACKING_SYNC_URL = "https://YOUR_FUNCTION.azurewebsites.net";
var TRACKING_API_KEY  = "your-secret-key";

var DASHBOARD_TAB     = "Dashboard";
var DETAIL_TAB_PREFIX = "";  // set to e.g. "Detail - " to create per-campaign tabs

// ═══════════════════════════════════════════════════════════════════
// MENU
// ═══════════════════════════════════════════════════════════════════

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("📊 Tracking")
    .addItem("📊 Sync Dashboard",     "syncDashboard")
    .addSeparator()
    .addItem("🔄 Sync All Campaigns", "syncAllCampaigns")
    .addToUi();
}

// ═══════════════════════════════════════════════════════════════════
// HELPER: format ISO timestamp to readable local time
// ═══════════════════════════════════════════════════════════════════

function formatTime(isoString) {
  if (!isoString) return "";
  try { return new Date(isoString).toLocaleString(); }
  catch (e) { return isoString; }
}

// ═══════════════════════════════════════════════════════════════════
// MAIN: Sync the dashboard tab (summary view)
// ═══════════════════════════════════════════════════════════════════

function syncDashboard() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(DASHBOARD_TAB);
  if (!sheet) {
    sheet = ss.insertSheet(DASHBOARD_TAB);
    // Move it to the first position
    ss.setActiveSheet(sheet);
    ss.moveActiveSheet(1);
  }

  // --- Fetch from Azure ---
  var url = TRACKING_SYNC_URL + "/api/dashboard?api_key=" +
            encodeURIComponent(TRACKING_API_KEY);

  var response;
  try {
    response = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  } catch (e) {
    SpreadsheetApp.getUi().alert("Cannot reach Azure.\n\nCheck TRACKING_SYNC_URL.\n\n" + e.toString());
    return;
  }

  if (response.getResponseCode() === 401) {
    SpreadsheetApp.getUi().alert("Invalid TRACKING_API_KEY. Update it in Apps Script.");
    return;
  }
  if (response.getResponseCode() !== 200) {
    SpreadsheetApp.getUi().alert("Azure error " + response.getResponseCode() + "\n\n" +
                                 response.getContentText().substring(0, 500));
    return;
  }

  var data = JSON.parse(response.getContentText());
  var totals = data.totals || {};

  // --- Clear and rebuild ---
  sheet.clear();

  var r = 1;

  // Title
  sheet.getRange(r, 1, 1, 6)
    .setValue("📊 SpeakHire Email Tracking")
    .setFontSize(18).setFontWeight("bold").merge();
  r += 1;
  sheet.getRange(r, 1, 1, 6)
    .setValue("Last updated: " + new Date().toLocaleString() +
              "  •  Past " + (data.days || 30) + " days  •  " +
              (totals.campaigns || 0) + " campaigns")
    .setFontSize(10).setForegroundColor("#999999").merge();
  r += 2;

  // ── Summary cards ──
  var cards = [
    { icon: "👁️", label: "Total Opens",  value: totals.opens || 0,             color: "#1a7a1a" },
    { icon: "🖱️", label: "Total Clicks", value: totals.clicks || 0,            color: "#1a5c9e" },
    { icon: "👤", label: "Unique People", value: totals.unique_recipients || 0, color: "#333333" },
    { icon: "🏢", label: "Orgs Reached", value: totals.orgs_reached || 0,      color: "#333333" },
    { icon: "📈", label: "Click Rate",   value: (totals.unique_recipients > 0
      ? Math.round(totals.clicks / totals.unique_recipients * 100) + "%"
      : "—"),  color: "#7a1a9e" },
  ];

  for (var ci = 0; ci < cards.length; ci++) {
    var col = ci + 1;
    var card = cards[ci];
    sheet.getRange(r, col)
      .setValue(card.icon + " " + card.label)
      .setFontSize(10).setForegroundColor("#888888");
    sheet.getRange(r + 1, col)
      .setValue(card.value)
      .setFontSize(28).setFontWeight("bold").setFontColor(card.color);
  }
  r += 4;

  // ── Campaign breakdown table ──
  sheet.getRange(r, 1).setValue("By Campaign").setFontSize(12).setFontWeight("bold");
  r += 1;

  var headers = ["Campaign", "Opens", "Clicks", "Unique People", "Orgs", "Open Rate"];
  for (var hi = 0; hi < headers.length; hi++) {
    var hCell = sheet.getRange(r, hi + 1);
    hCell.setValue(headers[hi]).setFontWeight("bold").setBackgroundColor("#f5f5f5");
  }
  r += 1;

  var nameMap = {
    "speaking_my_name": "#SpeakingMyName",
    "summit":           "Summit 2026",
    "soiree":           "Soirée 2026",
    "general":          "General Outreach",
  };

  var campaigns = data.campaigns || [];
  for (var ci2 = 0; ci2 < campaigns.length; ci2++) {
    var c = campaigns[ci2];
    var unique = c.unique_recipients || 0;
    var rate = unique > 0 ? Math.round(c.total_opens / unique * 100) + "%" : "—";

    sheet.getRange(r, 1).setValue(nameMap[c.campaign] || c.campaign);
    sheet.getRange(r, 2).setValue(c.total_opens);
    sheet.getRange(r, 3).setValue(c.total_clicks);
    sheet.getRange(r, 4).setValue(unique);
    sheet.getRange(r, 5).setValue(c.orgs_reached);
    sheet.getRange(r, 6).setValue(rate);
    r += 1;
  }

  // ── Daily trend (last 14 days) ──
  r += 1;
  sheet.getRange(r, 1).setValue("Daily Trend").setFontSize(12).setFontWeight("bold");
  r += 1;

  // Merge daily data across all campaigns
  var dailyAll = {};
  for (var ci3 = 0; ci3 < campaigns.length; ci3++) {
    var camp = campaigns[ci3];
    var daily = camp.daily || {};
    for (var date in daily) {
      if (!dailyAll[date]) dailyAll[date] = { opens: 0, clicks: 0 };
      dailyAll[date].opens += daily[date].opens || 0;
      dailyAll[date].clicks += daily[date].clicks || 0;
    }
  }

  // Sort dates, take last 14
  var sortedDates = Object.keys(dailyAll).sort();
  var recentDates = sortedDates.slice(-14);

  var trendHeaders = ["Date"];
  for (var di = 0; di < recentDates.length; di++) {
    trendHeaders.push(recentDates[di]);
  }
  sheet.getRange(r, 1).setValue("Opens").setFontWeight("bold").setBackgroundColor("#f5f5f5");
  for (var di2 = 0; di2 < recentDates.length; di2++) {
    var d = recentDates[di2];
    sheet.getRange(r, di2 + 2).setValue(dailyAll[d].opens).setHorizontalAlignment("center");
  }
  r += 1;
  sheet.getRange(r, 1).setValue("Clicks").setFontWeight("bold").setBackgroundColor("#f5f5f5");
  for (var di3 = 0; di3 < recentDates.length; di3++) {
    var d2 = recentDates[di3];
    sheet.getRange(r, di3 + 2).setValue(dailyAll[d2].clicks).setHorizontalAlignment("center");
  }

  // ── Recent activity feed ──
  r += 2;
  sheet.getRange(r, 1).setValue("Recent Activity").setFontSize(12).setFontWeight("bold");
  r += 1;

  var actHeaders = ["Time", "Campaign", "Event", "Recipient", "Organization"];
  for (var ai = 0; ai < actHeaders.length; ai++) {
    sheet.getRange(r, ai + 1).setValue(actHeaders[ai])
      .setFontWeight("bold").setBackgroundColor("#f5f5f5");
  }
  r += 1;

  var recent = data.recent_activity || [];
  for (var ri = 0; ri < recent.length; ri++) {
    var ev = recent[ri];
    sheet.getRange(r, 1).setValue(formatTime(ev.time));
    sheet.getRange(r, 2).setValue(nameMap[ev.campaign] || ev.campaign);
    sheet.getRange(r, 3).setValue((ev.event === "open" ? "👁️ Open" : "🖱️ Click"));
    sheet.getRange(r, 4).setValue(ev.email);
    sheet.getRange(r, 5).setValue(ev.org);
    r += 1;
  }

  // ── Column widths ──
  sheet.setColumnWidth(1, 170);
  sheet.setColumnWidth(2, 140);
  sheet.setColumnWidth(3, 110);
  sheet.setColumnWidth(4, 260);
  sheet.setColumnWidth(5, 220);
  sheet.setColumnWidth(6, 110);

  sheet.setFrozenRows(1);

  SpreadsheetApp.getUi().alert(
    "Dashboard refreshed!\n\n" +
    "Opens: "   + (totals.opens || 0)             + "\n" +
    "Clicks: "  + (totals.clicks || 0)            + "\n" +
    "People: "  + (totals.unique_recipients || 0) + "\n" +
    "Orgs: "    + (totals.orgs_reached || 0),
    SpreadsheetApp.getUi().ButtonSet.OK
  );
}

// ═══════════════════════════════════════════════════════════════════
// SYNC ALL CAMPAIGNS — Creates a per-campaign detail tab for each
// ═══════════════════════════════════════════════════════════════════

function syncAllCampaigns() {
  // The campaigns we track
  var campaigns = ["speaking_my_name", "summit", "soiree", "general"];

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var totalUpdated = 0;

  for (var ci = 0; ci < campaigns.length; ci++) {
    var campaign = campaigns[ci];

    // Get email-keyed stats from Azure
    var url = TRACKING_SYNC_URL + "/api/sheet/" + campaign +
              "?api_key=" + encodeURIComponent(TRACKING_API_KEY);

    try {
      var response = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
      if (response.getResponseCode() !== 200) continue;

      var data = JSON.parse(response.getContentText());
      var emails = data.emails || {};
      var emailList = Object.keys(emails);

      if (emailList.length === 0) continue;

      // Create or get the detail tab for this campaign
      var tabName = (DETAIL_TAB_PREFIX || "Detail - ") + campaign;
      var sheet = ss.getSheetByName(tabName);
      if (!sheet) {
        sheet = ss.insertSheet(tabName);
      } else {
        sheet.clear();
      }

      // Headers
      var headers = ["Email", "Opens", "Clicks", "Last Open", "Last Click"];
      for (var hi = 0; hi < headers.length; hi++) {
        sheet.getRange(1, hi + 1).setValue(headers[hi])
          .setFontWeight("bold").setBackgroundColor("#f5f5f5");
      }

      // Rows
      var r = 2;
      for (var ei = 0; ei < emailList.length; ei++) {
        var email = emailList[ei];
        var stats = emails[email];

        sheet.getRange(r, 1).setValue(email);
        sheet.getRange(r, 2).setValue(stats.opens || 0);
        sheet.getRange(r, 3).setValue(stats.clicks || 0);
        sheet.getRange(r, 4).setValue(formatTime(stats.last_open));
        sheet.getRange(r, 5).setValue(formatTime(stats.last_click));
        r++;
        totalUpdated++;
      }

      sheet.setColumnWidth(1, 280);
      sheet.setColumnWidth(2, 70);
      sheet.setColumnWidth(3, 70);
      sheet.setColumnWidth(4, 170);
      sheet.setColumnWidth(5, 170);
      sheet.setFrozenRows(1);

    } catch (e) {
      Logger.log("Failed to sync " + campaign + ": " + e);
    }
  }

  SpreadsheetApp.getUi().alert(
    "All campaigns synced!\n\n" +
    "Total emails tracked: " + totalUpdated + "\n\n" +
    "Detail tabs created for campaigns with data.",
    SpreadsheetApp.getUi().ButtonSet.OK
  );
}
