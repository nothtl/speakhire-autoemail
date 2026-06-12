/**
 * Tracking Sync — pulls open/click data from Azure back into Google Sheet
 *
 * Paste this INTO your existing send script (at the end, before or after
 * the onOpen function). It adds a "Sync Tracking" menu item and writes
 * open/click counts to new columns in the sheet.
 *
 * REQUIRES these variables from the parent send script:
 *   TRACKING_BASE_URL — your Azure Function URL
 *   CAMPAIGN_SLUG     — e.g. "soiree", "speaking_my_name", "summit"
 *
 * CHANGE THIS to your Azure API key:
 */
var TRACKING_API_KEY = "your-secret-key";  // same as TRACKING_API_KEY in Azure

// Columns where opens/clicks will be written.
// These are appended AFTER your existing columns.
// Adjust if your sheet already has these columns at different positions.
var TRACKING_COL_OPENS  = null;  // set to a number, or null to auto-detect
var TRACKING_COL_CLICKS = null;  // set to a number, or null to auto-detect

// ═══════════════════════════════════════════════════════════════════════════
// MAIN: Sync tracking data from Azure → Google Sheet
// ═══════════════════════════════════════════════════════════════════════════

function syncTracking() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  if (!sheet) {
    SpreadsheetApp.getUi().alert('Sheet "' + SHEET_NAME + '" not found.');
    return;
  }

  // --- Determine which column has the email address ---
  // Each campaign has a different email column. Detect by sheet name.
  var emailCol = getEmailColumn(sheet);

  // --- Determine opens/clicks columns ---
  var opensCol = TRACKING_COL_OPENS || getOrCreateTrackingColumn(sheet, "Opens");
  var clicksCol = TRACKING_COL_CLICKS || getOrCreateTrackingColumn(sheet, "Clicks");
  var lastOpenCol = getOrCreateTrackingColumn(sheet, "Last Open");
  var lastClickCol = getOrCreateTrackingColumn(sheet, "Last Click");

  // --- Read all rows ---
  var lastRow = sheet.getLastRow();
  var lastCol = Math.max(emailCol, opensCol, clicksCol, lastOpenCol, lastClickCol);
  var data = sheet.getRange(1, 1, lastRow, lastCol).getValues();

  // --- Collect all email addresses ---
  var emails = [];
  var emailRowMap = {};  // email → row number (1-based)
  for (var i = 1; i < data.length; i++) {  // skip header
    var email = String(data[i][emailCol - 1] || "").trim().toLowerCase();
    if (email && email.indexOf("@") !== -1) {
      emails.push(email);
      emailRowMap[email] = i + 1;  // sheet rows are 1-based, data[i] is row i+1
    }
  }

  if (emails.length === 0) {
    SpreadsheetApp.getUi().alert("No email addresses found in column " + emailCol);
    return;
  }

  // --- Call Azure ---
  var url = TRACKING_BASE_URL + "/api/sheet/" + CAMPAIGN_SLUG +
            "?api_key=" + encodeURIComponent(TRACKING_API_KEY) +
            "&emails=" + encodeURIComponent(emails.join(","));

  var response;
  try {
    response = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  } catch (e) {
    SpreadsheetApp.getUi().alert("Azure unreachable.\n\n" + e.toString());
    return;
  }

  if (response.getResponseCode() === 401) {
    SpreadsheetApp.getUi().alert("Invalid TRACKING_API_KEY. Check the key in sync_tracking.js matches Azure.");
    return;
  }
  if (response.getResponseCode() !== 200) {
    SpreadsheetApp.getUi().alert("Azure returned " + response.getResponseCode() + ".\n\n" +
                                 response.getContentText().substring(0, 500));
    return;
  }

  var result = JSON.parse(response.getContentText());
  var emailStats = result.emails || {};

  // --- Write back to sheet ---
  var updated = 0;
  for (var email in emailStats) {
    var row = emailRowMap[email];
    if (!row) continue;

    var stats = emailStats[email];

    if (stats.opens > 0) {
      sheet.getRange(row, opensCol).setValue(stats.opens);
    }
    if (stats.clicks > 0) {
      sheet.getRange(row, clicksCol).setValue(stats.clicks);
    }
    if (stats.last_open) {
      sheet.getRange(row, lastOpenCol).setValue(formatTimestamp(stats.last_open));
    }
    if (stats.last_click) {
      sheet.getRange(row, lastClickCol).setValue(formatTimestamp(stats.last_click));
    }
    updated++;
  }

  // --- Summary ---
  var totalOpens = 0, totalClicks = 0;
  for (var e in emailStats) {
    totalOpens += emailStats[e].opens || 0;
    totalClicks += emailStats[e].clicks || 0;
  }

  SpreadsheetApp.getUi().alert(
    "Tracking synced!\n\n" +
    "Rows updated: " + updated + "\n" +
    "Total opens:  " + totalOpens + "\n" +
    "Total clicks: " + totalClicks,
    SpreadsheetApp.getUi().ButtonSet.OK
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// HELPERS
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Detect which column has the email address based on sheet name.
 * Falls back to scanning the header row for "email" in the column name.
 */
function getEmailColumn(sheet) {
  // Try to use the column constants from the parent send script
  // smn_send.js:     COL_EMAIL = 5
  // soiree_send.js:  COL_EMAIL = 4
  // outreach_send.js: COL_EMAIL = 4
  // apps_script_send.js: COL_EMAIL = 3
  if (typeof COL_EMAIL !== "undefined") return COL_EMAIL;

  // Fallback: scan header row for a column containing "email"
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  for (var i = 0; i < headers.length; i++) {
    var h = String(headers[i] || "").toLowerCase();
    if (h.indexOf("email") !== -1 && h.indexOf("subject") === -1) {
      return i + 1;
    }
  }
  return 1;  // desperate fallback
}

/**
 * Find an existing tracking column by header name, or create it
 * as the next available column.
 */
function getOrCreateTrackingColumn(sheet, columnName) {
  var lastCol = sheet.getLastColumn();
  var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0];

  // Search for existing header
  for (var i = 0; i < headers.length; i++) {
    if (String(headers[i] || "").trim().toLowerCase() === columnName.toLowerCase()) {
      return i + 1;
    }
  }

  // Not found — create it
  var newCol = lastCol + 1;
  sheet.getRange(1, newCol).setValue(columnName);
  sheet.getRange(1, newCol).setFontWeight("bold");
  return newCol;
}

/**
 * Convert ISO timestamp to a readable local date/time string.
 */
function formatTimestamp(isoString) {
  if (!isoString) return "";
  try {
    var d = new Date(isoString);
    return d.toLocaleString();
  } catch (e) {
    return isoString;
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// DASHBOARD — populates a "Tracking Dashboard" tab with aggregate stats
// ═══════════════════════════════════════════════════════════════════════════

var DASHBOARD_TAB = "Tracking Dashboard";

function syncDashboard() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(DASHBOARD_TAB);

  // Create tab if it doesn't exist
  if (!sheet) {
    sheet = ss.insertSheet(DASHBOARD_TAB);
  }

  // --- Fetch from Azure ---
  var url = TRACKING_BASE_URL + "/api/dashboard?api_key=" +
            encodeURIComponent(TRACKING_API_KEY);

  var response;
  try {
    response = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  } catch (e) {
    SpreadsheetApp.getUi().alert("Azure unreachable.\n\n" + e.toString());
    return;
  }

  if (response.getResponseCode() !== 200) {
    SpreadsheetApp.getUi().alert("Azure returned " + response.getResponseCode() +
                                 ".\n\n" + response.getContentText().substring(0, 500));
    return;
  }

  var data = JSON.parse(response.getContentText());

  // --- Clear old content ---
  sheet.clear();

  // --- Style helpers ---
  var headerStyle = { bold: true, fontSize: 14 };
  var cardStyle   = { bold: true, fontSize: 28 };
  var subStyle    = { bold: false, fontSize: 11, foregroundColor: "#666666" };
  var greenStyle  = { bold: true, foregroundColor: "#1a7a1a" };
  var blueStyle   = { bold: true, foregroundColor: "#1a5c9e" };

  var r = 1;  // current row

  // ==== ROW 1: Title ====
  sheet.getRange(r, 1).setValue("📊 Email Tracking Dashboard").setFontSize(16).setFontWeight("bold");
  sheet.getRange(r, 1, 1, 6).merge();
  r += 1;
  sheet.getRange(r, 1).setValue("Last synced: " + new Date().toLocaleString() +
                                 "  |  Past " + (data.days || 30) + " days")
      .setFontSize(10).setForegroundColor("#999999");
  sheet.getRange(r, 1, 1, 6).merge();
  r += 2;

  // ==== ROW 3: Summary cards ====
  var totals = data.totals || {};
  var cards = [
    ["👁️  Total Opens",     String(totals.opens || 0),             greenStyle],
    ["🖱️  Total Clicks",    String(totals.clicks || 0),            blueStyle],
    ["👤  Unique Recipients", String(totals.unique_recipients || 0), {}],
    ["🏢  Orgs Reached",     String(totals.orgs_reached || 0),     {}],
    ["📣  Campaigns",        String(totals.campaigns || 0),        {}],
  ];

  for (var ci = 0; ci < cards.length; ci++) {
    var col = ci + 1;
    sheet.getRange(r, col).setValue(cards[ci][0]).setFontSize(10).setForegroundColor("#666666");
    sheet.getRange(r + 1, col).setValue(cards[ci][1]).setFontSize(28).setFontWeight("bold");
    if (Object.keys(cards[ci][2]).length > 0) {
      sheet.getRange(r + 1, col).setFontColor(cards[ci][2].foregroundColor);
    }
  }
  r += 4;

  // ==== Per-campaign table ====
  sheet.getRange(r, 1).setValue("Campaigns").setFontSize(13).setFontWeight("bold");
  r += 1;

  var tableHeaders = ["Campaign", "Opens", "Clicks", "Unique Recipients", "Orgs Reached"];
  for (var hi = 0; hi < tableHeaders.length; hi++) {
    var cell = sheet.getRange(r, hi + 1);
    cell.setValue(tableHeaders[hi]);
    cell.setFontWeight("bold");
    cell.setBackgroundColor("#f0f0f0");
  }
  r += 1;

  var campaigns = data.campaigns || [];
  for (var ci2 = 0; ci2 < campaigns.length; ci2++) {
    var c = campaigns[ci2];
    // Friendly campaign names
    var nameMap = {
      "speaking_my_name": "#SpeakingMyName",
      "summit": "Summit 2026",
      "soiree": "Soirée 2026",
      "general": "General Outreach",
    };
    var displayName = nameMap[c.campaign] || c.campaign;

    sheet.getRange(r, 1).setValue(displayName);
    sheet.getRange(r, 2).setValue(c.total_opens);
    sheet.getRange(r, 3).setValue(c.total_clicks);
    sheet.getRange(r, 4).setValue(c.unique_recipients);
    sheet.getRange(r, 5).setValue(c.orgs_reached);
    r += 1;
  }

  // ==== Recent activity ====
  r += 1;
  sheet.getRange(r, 1).setValue("Recent Activity").setFontSize(13).setFontWeight("bold");
  r += 1;

  var actHeaders = ["Time", "Campaign", "Event", "Recipient", "Org"];
  for (var ai = 0; ai < actHeaders.length; ai++) {
    var aCell = sheet.getRange(r, ai + 1);
    aCell.setValue(actHeaders[ai]);
    aCell.setFontWeight("bold");
    aCell.setBackgroundColor("#f0f0f0");
  }
  r += 1;

  var recent = data.recent_activity || [];
  for (var ri = 0; ri < recent.length; ri++) {
    var ev = recent[ri];
    var eventIcon = ev.event === "open" ? "👁️" : "🖱️";
    sheet.getRange(r, 1).setValue(formatTimestamp(ev.time));
    sheet.getRange(r, 2).setValue(ev.campaign);
    sheet.getRange(r, 3).setValue(eventIcon + " " + ev.event);
    sheet.getRange(r, 4).setValue(ev.email);
    sheet.getRange(r, 5).setValue(ev.org);
    r += 1;
  }

  // --- Column widths ---
  sheet.setColumnWidth(1, 160);
  sheet.setColumnWidth(2, 130);
  sheet.setColumnWidth(3, 100);
  sheet.setColumnWidth(4, 260);
  sheet.setColumnWidth(5, 220);

  // --- Freeze header ---
  sheet.setFrozenRows(1);

  SpreadsheetApp.getUi().alert(
    "Dashboard updated!\n\n" +
    "Opens: " + (totals.opens || 0) + "\n" +
    "Clicks: " + (totals.clicks || 0) + "\n" +
    "Recipients: " + (totals.unique_recipients || 0),
    SpreadsheetApp.getUi().ButtonSet.OK
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// MENU — ADD THESE TO YOUR onOpen() FUNCTION:
// ═══════════════════════════════════════════════════════════════════════════

/*
 * In your existing onOpen() function, add these lines inside createMenu():
 *
 *   .addItem("🔄 Sync Tracking", "syncTracking")
 *   .addItem("📊 Sync Dashboard", "syncDashboard")
 *
 * Example:
 *
 * function onOpen() {
 *   var ui = SpreadsheetApp.getUi();
 *   ui.createMenu("#SpeakingMyName")
 *     ...
 *     .addItem("🔄 Sync Tracking", "syncTracking")
 *     .addItem("📊 Sync Dashboard", "syncDashboard")
 *     .addToUi();
 * }
 */
