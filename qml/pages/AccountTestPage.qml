/*
 * Ferry - account connection test result page.
 * Replaces the account dialog on the page stack when it is accepted. The
 * dialog starts the save + connection test; this page follows it through the
 * 'account-status' and 'account-result' events and shows the live progress
 * and then the full result (steps, account data, and the top level entries
 * found - libraries on Seafile, folders on Nextcloud). Going back
 * returns to the page from which the account dialog was opened.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

import QtQuick 2.0
import Sailfish.Silica 1.0
import io.thp.pyotherside 1.5

Page {
    id: page

    property bool running: true
    property string statusText: qsTr("Saving account...")
    property bool finished: false
    property bool ok: false
    property string message: ""
    property string details: ""
    property var accountRows: []
    // Backend wording for the remote top level ({key, one, many} from Python).
    property var remoteTerms: ({})

    Terminology {
        id: terms
        source: page.remoteTerms
    }

    readonly property color okColor: "#66cc66"
    readonly property color failColor: "#ff6666"

    ListModel { id: stepModel }
    ListModel { id: libraryModel }

    function buildAccountRows(account) {
        var rows = [];
        if (!account || !account.url) {
            return rows;
        }
        rows.push({label: qsTr("Server"),
                   value: account.display_url || account.url});
        if (account.url && account.url !== (account.display_url || account.url)) {
            // The address rclone actually uses. It differs from the line
            // above wherever a backend stores a technical URL (Nextcloud
            // keeps the full WebDAV path), and that is exactly the value a
            // failing connection has to be judged by - without it a bug
            // report says nothing about where the app was pointed.
            rows.push({label: qsTr("Full URL"), value: account.url});
        }
        rows.push({label: qsTr("User"), value: account.user || ""});
        rows.push({label: qsTr("Backend"), value: account.backend || ""});
        if (account.insecure_tls) {
            // A row only for the switched off check: verifying the
            // certificate is the normal case and needs no mention, not
            // verifying it does.
            rows.push({label: qsTr("Certificate check"),
                       value: qsTr("Off - any certificate is accepted")});
        }
        rows.push({label: qsTr("Two-factor auth"),
                   value: account.use_2fa ? qsTr("On") : qsTr("Off")});
        rows.push({label: qsTr("Configuration"),
                   value: account.encrypted ? qsTr("Encrypted")
                                            : qsTr("Not encrypted")});
        return rows;
    }

    function applyResult(result) {
        page.running = false;
        page.finished = true;
        page.ok = !!result.ok;
        page.message = result.message || "";
        page.details = result.details || "";
        page.accountRows = buildAccountRows(result.account);
        if (result.account && result.account.terms) {
            page.remoteTerms = result.account.terms;
        }

        stepModel.clear();
        var steps = result.steps || [];
        for (var i = 0; i < steps.length; i++) {
            stepModel.append({title: steps[i].title,
                              stepOk: !!steps[i].ok,
                              detail: steps[i].detail || ""});
        }

        libraryModel.clear();
        var libraries = result.libraries || [];
        for (var j = 0; j < libraries.length; j++) {
            libraryModel.append({name: libraries[j]});
        }
    }

    SilicaListView {
        id: listView
        anchors.fill: parent
        model: libraryModel

        PullDownMenu {
            busy: page.running
            MenuItem {
                text: qsTr("Test again")
                enabled: !page.running
                onClicked: python.retest()
            }
            MenuItem {
                text: qsTr("Edit account")
                enabled: !page.running
                onClicked: pageStack.replace(Qt.resolvedUrl("AccountPage.qml"))
            }
        }

        header: Column {
            width: listView.width
            spacing: Theme.paddingMedium

            PageHeader {
                title: qsTr("Connection test")
                description: page.running ? qsTr("Running...")
                           : (page.ok ? qsTr("Successful") : qsTr("Failed"))
            }

            // --- live progress -------------------------------------------
            Row {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * Theme.horizontalPageMargin
                visible: page.running
                spacing: Theme.paddingMedium

                BusyIndicator {
                    running: page.running
                    size: BusyIndicatorSize.Small
                    anchors.verticalCenter: parent.verticalCenter
                }

                Label {
                    width: parent.width - Theme.itemSizeSmall
                    anchors.verticalCenter: parent.verticalCenter
                    wrapMode: Text.WordWrap
                    color: Theme.highlightColor
                    text: page.statusText
                }
            }

            // --- result summary ------------------------------------------
            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * Theme.horizontalPageMargin
                visible: page.finished
                // Text.Wrap, not WordWrap: a backend's hint for a wrong
                // path names an example URL, which has no spaces to break
                // at and would otherwise run off the screen.
                wrapMode: Text.Wrap
                font.pixelSize: Theme.fontSizeLarge
                color: page.ok ? page.okColor : page.failColor
                text: page.message
            }

            // --- account -------------------------------------------------
            SectionHeader {
                text: qsTr("Account")
                visible: page.accountRows.length > 0
            }

            Repeater {
                model: page.accountRows

                delegate: Column {
                    x: Theme.horizontalPageMargin
                    width: listView.width - 2 * Theme.horizontalPageMargin

                    Label {
                        text: modelData.label
                        font.pixelSize: Theme.fontSizeExtraSmall
                        color: Theme.secondaryColor
                    }

                    Label {
                        width: parent.width
                        text: modelData.value
                        wrapMode: Text.WrapAnywhere
                        font.pixelSize: Theme.fontSizeSmall
                        color: Theme.highlightColor
                    }
                }
            }

            // --- steps ---------------------------------------------------
            SectionHeader {
                text: qsTr("Test details")
                visible: stepModel.count > 0
            }

            Repeater {
                model: stepModel

                delegate: Column {
                    x: Theme.horizontalPageMargin
                    width: listView.width - 2 * Theme.horizontalPageMargin

                    Row {
                        spacing: Theme.paddingMedium

                        Label {
                            text: stepOk ? "✓" : "✗"
                            font.bold: true
                            color: stepOk ? page.okColor : page.failColor
                        }

                        Label {
                            text: title
                            color: Theme.primaryColor
                        }
                    }

                    Label {
                        width: parent.width
                        visible: detail.length > 0
                        text: detail
                        wrapMode: Text.WrapAnywhere
                        font.pixelSize: Theme.fontSizeExtraSmall
                        color: Theme.secondaryColor
                    }
                }
            }

            // --- error details -------------------------------------------
            Column {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * Theme.horizontalPageMargin
                visible: page.finished && !page.ok && page.details.length > 0
                spacing: Theme.paddingSmall

                SectionHeader { text: qsTr("Error output") }

                Label {
                    width: parent.width
                    text: page.details
                    wrapMode: Text.WrapAnywhere
                    font.family: "monospace"
                    font.pixelSize: Theme.fontSizeExtraSmall
                    color: Theme.secondaryColor
                }
            }

            // --- top level of the remote ---------------------------------
            SectionHeader {
                text: page.finished && page.ok ? terms.counted(libraryModel.count)
                                               : terms.many
                visible: page.finished && page.ok
            }

            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * Theme.horizontalPageMargin
                visible: page.finished && page.ok && libraryModel.count === 0
                wrapMode: Text.WordWrap
                color: Theme.secondaryColor
                text: terms.emptyAccount
            }
        }

        delegate: ListItem {
            width: listView.width
            contentHeight: Theme.itemSizeSmall

            Image {
                id: libraryIcon
                x: Theme.horizontalPageMargin
                anchors.verticalCenter: parent.verticalCenter
                source: "image://theme/icon-m-folder"
            }

            Label {
                anchors {
                    left: libraryIcon.right
                    leftMargin: Theme.paddingMedium
                    right: parent.right
                    rightMargin: Theme.horizontalPageMargin
                    verticalCenter: parent.verticalCenter
                }
                text: name
                truncationMode: TruncationMode.Fade
                color: Theme.primaryColor
            }
        }

        footer: Item { width: 1; height: Theme.paddingLarge }

        VerticalScrollDecorator { }
    }

    Python {
        id: python

        function reset(text) {
            page.running = true;
            page.finished = false;
            page.statusText = text;
            page.accountRows = [];
            stepModel.clear();
            libraryModel.clear();
        }

        function retest() {
            reset(qsTr("Testing connection..."));
            call('config_manager.test_connection_background', [], function() {});
        }

        Component.onCompleted: {
            addImportPath(Qt.resolvedUrl('../utilities'));

            // Progress messages from config_manager while the work runs.
            setHandler('account-status', function(text) {
                if (page.running) {
                    page.statusText = text;
                }
            });

            setHandler('account-result', function(result) {
                page.applyResult(result);
            });

            // The save+test job itself is started by the account dialog when
            // it is accepted; this page only reports what comes back.
            importModule('config_manager', function() {});
        }

        onError: {
            console.log('[ferry] python error: ' + traceback);
            page.running = false;
            page.finished = true;
            page.ok = false;
            page.message = qsTr("Python error - see log");
        }
    }
}
