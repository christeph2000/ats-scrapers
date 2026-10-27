// Opens the settings page from the toolbar button, the panel's Settings link,
// and on first install.
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === "openOptions") chrome.runtime.openOptionsPage();
});
chrome.runtime.onInstalled.addListener((d) => {
  if (d.reason === "install") chrome.runtime.openOptionsPage();
});
