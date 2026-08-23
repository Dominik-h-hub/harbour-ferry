/*
 * Ferry - settings dialog.
 * Account, sync interval, network rule, exclude patterns,
 * max-delete threshold, diagnostics entry.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

import QtQuick 2.0
import Sailfish.Silica 1.0
import io.thp.pyotherside 1.5

Dialog {
    id: page

    property string accountUser: ""
    property string accountUrl: ""
    property bool settingsLoaded: false
    property string timerInfo: ""
    property bool modulesReady: false
    property string appVersion: ""

    canAccept: settingsLoaded
    onAccepted: python.applyAll()

    // Fires when the dialog is first shown and on every return to it, so
    // the summary is loaded exactly once per visit. Loading it from
    // Component.onCompleted as well would double every request - including
    // the two systemctl processes behind timer_manager.get_status().
    onStatusChanged: {
        if (status === PageStatus.Active) {
            python.refreshSummary();
        }
    }

    SilicaFlickable {
        anchors.fill: parent
        contentHeight: column.height

        Column {
            id: column
            width: page.width

            DialogHeader {
                acceptText: qsTr("Save")
                title: qsTr("Settings")
            }

            SectionHeader { text: qsTr("Server") }

            BackgroundItem {
                id: accountItem
                width: parent.width
                // Both: contentHeight sizes the highlight, height makes the
                // entry grow with the account lines - without it a wrapped
                // URL runs into the next section header.
                contentHeight: accountColumn.height + 2 * Theme.paddingMedium
                height: contentHeight
                onClicked: pageStack.push(Qt.resolvedUrl("AccountPage.qml"))

                Column {
                    id: accountColumn
                    x: Theme.horizontalPageMargin
                    width: parent.width - 2 * Theme.horizontalPageMargin
                    anchors.verticalCenter: parent.verticalCenter

                    Label {
                        text: qsTr("Account")
                        color: accountItem.highlighted ? Theme.highlightColor
                                                       : Theme.primaryColor
                    }

                    Label {
                        width: parent.width
                        visible: page.accountUser.length > 0
                        font.pixelSize: Theme.fontSizeSmall
                        color: Theme.secondaryHighlightColor
                        truncationMode: TruncationMode.Fade
                        text: page.accountUser
                    }

                    Label {
                        width: parent.width
                        font.pixelSize: Theme.fontSizeSmall
                        color: Theme.secondaryColor
                        wrapMode: Text.WrapAnywhere
                        text: page.accountUrl.length > 0 ? page.accountUrl
                                                         : qsTr("Not configured")
                    }
                }
            }

            SectionHeader { text: qsTr("Synchronization") }

            ComboBox {
                id: intervalCombo
                label: qsTr("Background sync")
                description: page.timerInfo
                menu: ContextMenu {
                    MenuItem { text: qsTr("Manual only") }
                    MenuItem { text: qsTr("Every 5 minutes") }
                    MenuItem { text: qsTr("Every 15 minutes") }
                    MenuItem { text: qsTr("Every 30 minutes") }
                    MenuItem { text: qsTr("Every hour") }
                    MenuItem { text: qsTr("Every 6 hours") }
                    MenuItem { text: qsTr("Every 12 hours") }
                }
            }

            ComboBox {
                id: networkCombo
                label: qsTr("Allowed network")
                menu: ContextMenu {
                    MenuItem { text: qsTr("Wi-Fi only") }
                    MenuItem { text: qsTr("Wi-Fi and mobile data") }
                }
            }

            Slider {
                id: maxDeleteSlider
                // Keep the explicit width: without it Silica does not size
                // the slider to the column at all. It makes Silica's internal
                // _extraPadding binding warn about a loop, which Qt breaks by
                // itself - a harmless warning, unlike a broken layout.
                width: parent.width
                minimumValue: 10
                maximumValue: 100
                stepSize: 5
                label: qsTr("Safety limit: max. deletions per run")
                valueText: Math.round(value) + " %"
            }

            SectionHeader { text: qsTr("Exclude patterns") }

            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * Theme.horizontalPageMargin
                wrapMode: Text.WordWrap
                font.pixelSize: Theme.fontSizeExtraSmall
                color: Theme.secondaryColor
                text: qsTr("One pattern per line; applies to all folder syncs. Changes trigger a full resync of each pair.")
            }

            TextArea {
                id: excludesArea
                width: parent.width
                font.family: "monospace"
                font.pixelSize: Theme.fontSizeSmall
                placeholderText: qsTr("e.g. *.tmp")
            }

            SectionHeader { text: qsTr("Support") }

            BackgroundItem {
                id: diagItem
                width: parent.width
                onClicked: pageStack.push(Qt.resolvedUrl("DiagnosticsPage.qml"))

                Label {
                    x: Theme.horizontalPageMargin
                    anchors.verticalCenter: parent.verticalCenter
                    text: qsTr("Diagnostics")
                    color: diagItem.highlighted ? Theme.highlightColor : Theme.primaryColor
                }
            }

            // ── About ────────────────────────────────────
            SectionHeader {
                text: qsTr("About")
            }

            ListItem {
                contentHeight: Theme.itemSizeMedium
                _backgroundColor: "transparent"
                highlighted: false

                Label {
                    anchors.left: parent.left
                    anchors.leftMargin: Theme.horizontalPageMargin
                    anchors.verticalCenter: parent.verticalCenter
                    text: qsTr("App Version")
                    color: Theme.primaryColor
                }
                Label {
                    anchors.right: parent.right
                    anchors.rightMargin: Theme.horizontalPageMargin
                    anchors.verticalCenter: parent.verticalCenter
                    text: page.appVersion !== "" ? page.appVersion : "?.?.?"
                    color: Theme.secondaryColor
                }
            }

            Separator {
                width: parent.width
                color: Theme.primaryColor
                horizontalAlignment: Qt.AlignHCenter
            }

            ListItem {
                contentHeight: Theme.itemSizeMedium

                onClicked: Qt.openUrlExternally("https://github.com/Dominik-h-hub/harbour-ferry/issues")

                Label {
                    anchors.left: parent.left
                    anchors.leftMargin: Theme.horizontalPageMargin
                    anchors.verticalCenter: parent.verticalCenter
                    text: qsTr("Report a bug or request a feature")
                    color: Theme.primaryColor
                }
                Image {
                    anchors.right: parent.right
                    anchors.rightMargin: Theme.horizontalPageMargin
                    anchors.verticalCenter: parent.verticalCenter
                    source: "image://theme/icon-m-right"
                    width: Theme.iconSizeSmall
                    height: Theme.iconSizeSmall
                }
            }

            Separator {
                width: parent.width
                color: Theme.primaryColor
                horizontalAlignment: Qt.AlignHCenter
            }

            ListItem {
                contentHeight: Theme.itemSizeMedium

                onClicked: Qt.openUrlExternally("https://github.com/Dominik-h-hub/harbour-ferry/tree/main/translations")

                Label {
                    anchors.left: parent.left
                    anchors.leftMargin: Theme.horizontalPageMargin
                    anchors.verticalCenter: parent.verticalCenter
                    text: qsTr("Add a translation")
                    color: Theme.primaryColor
                }
                Image {
                    anchors.right: parent.right
                    anchors.rightMargin: Theme.horizontalPageMargin
                    anchors.verticalCenter: parent.verticalCenter
                    source: "image://theme/icon-m-right"
                    width: Theme.iconSizeSmall
                    height: Theme.iconSizeSmall
                }
            }

            Separator {
                width: parent.width
                color: Theme.primaryColor
                horizontalAlignment: Qt.AlignHCenter
            }

            ListItem {
                contentHeight: Theme.itemSizeMedium

                onClicked: Qt.openUrlExternally("https://github.com/Dominik-h-hub/harbour-ferry")

                Label {
                    anchors.left: parent.left
                    anchors.leftMargin: Theme.horizontalPageMargin
                    anchors.verticalCenter: parent.verticalCenter
                    text: qsTr("Code Repository")
                    color: Theme.primaryColor
                }
                Image {
                    anchors.right: parent.right
                    anchors.rightMargin: Theme.horizontalPageMargin
                    anchors.verticalCenter: parent.verticalCenter
                    source: "image://theme/icon-m-right"
                    width: Theme.iconSizeSmall
                    height: Theme.iconSizeSmall
                }
            }

            Separator {
                width: parent.width
                color: Theme.primaryColor
                horizontalAlignment: Qt.AlignHCenter
            }

            Item { width: 1; height: Theme.paddingLarge }
        }

        VerticalScrollDecorator { }
    }

    Python {
        id: python

        property var intervalKeys: ["manual", "5min", "15min", "30min", "1h", "6h", "12h"]

        function refreshSummary() {
            if (!page.modulesReady) {
                // Import still running - Component.onCompleted repeats the
                // call as soon as the modules are there.
                return;
            }
            call('config_manager.get_account_summary', [], function(summary) {
                if (summary && summary.error) {
                    page.accountUser = "";
                    page.accountUrl = summary.error;
                } else if (summary && summary.url) {
                    page.accountUser = summary.user;
                    // Short server URL - the stored one carries the backend
                    // specific path (Nextcloud: the full WebDAV path).
                    page.accountUrl = summary.display_url || summary.url;
                } else {
                    page.accountUser = "";
                    page.accountUrl = "";
                }
            });
            call('timer_manager.get_status', [], function(status) {
                page.timerInfo = status.active
                    ? (status.next_run.length > 0
                       ? qsTr("Next run: %1").arg(status.next_run) : qsTr("Active"))
                    : "";
            });
        }

        function loadSettings() {
            call('settings_manager.get_settings', [], function(settings) {
                intervalCombo.currentIndex =
                    Math.max(0, intervalKeys.indexOf(settings.interval));
                networkCombo.currentIndex = settings.network_rule === "any" ? 1 : 0;
                maxDeleteSlider.value = settings.max_delete;
                excludesArea.text = settings.excludes.join("\n");
                page.settingsLoaded = true;
            });
        }

        function applyAll() {
            // Detached call: the dialog closes now, the result arrives on
            // the main page via the 'settings-applied' event.
            call('settings_manager.apply_all_background', [{
                interval: intervalKeys[intervalCombo.currentIndex],
                network_rule: networkCombo.currentIndex === 1 ? "any" : "wifi",
                max_delete: Math.round(maxDeleteSlider.value),
                excludes_text: excludesArea.text
            }], function() {});
        }

        Component.onCompleted: {
            addImportPath(Qt.resolvedUrl('../utilities'));

            // Separate from the chain below: version.py imports nothing
            // else and the value never changes, so it must not delay the
            // settings - and a failure here only costs the version label.
            importModule('version', function() {
                call('version.app_version_full', [], function(fullVersion) {
                    page.appVersion = fullVersion;
                });
            });

            setHandler('account-result', function(result) {
                // The result itself is shown on the AccountTestPage; here we
                // only refresh the account summary shown in this dialog.
                refreshSummary();
            });

            importModule('config_manager', function() {
                importModule('settings_manager', function() {
                    importModule('timer_manager', function() {
                        page.modulesReady = true;
                        loadSettings();
                        if (page.status === PageStatus.Active) {
                            // onStatusChanged already fired (or will not
                            // fire again) - fetch the summary now.
                            refreshSummary();
                        }
                    });
                });
            });
        }

        onError: console.log('[ferry] python error: ' + traceback)
    }
}
