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

    canAccept: settingsLoaded
    onAccepted: python.applyAll()

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
                contentHeight: accountColumn.height + 2 * Theme.paddingMedium
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

            Item { width: 1; height: Theme.paddingLarge }
        }

        VerticalScrollDecorator { }
    }

    Python {
        id: python

        property var intervalKeys: ["manual", "5min", "15min", "30min", "1h", "6h", "12h"]

        function refreshSummary() {
            call('config_manager.get_account_summary', [], function(summary) {
                if (summary && summary.error) {
                    page.accountUser = "";
                    page.accountUrl = summary.error;
                } else if (summary && summary.url) {
                    page.accountUser = summary.user;
                    page.accountUrl = summary.url;
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

            setHandler('account-result', function(result) {
                // The result itself is shown on the AccountTestPage; here we
                // only refresh the account summary shown in this dialog.
                refreshSummary();
            });

            importModule('config_manager', function() {
                importModule('settings_manager', function() {
                    importModule('timer_manager', function() {
                        refreshSummary();
                        loadSettings();
                    });
                });
            });
        }

        onError: console.log('[ferry] python error: ' + traceback)
    }
}
