/*
 * Ferry - main page with two tabs:
 * left "Local syncs", right "Remote" (top level of the remote; tapping an
 * entry opens the remote browser). What that top level is called comes from
 * the backend: libraries on Seafile, folders on Nextcloud (Terminology.qml).
 * Custom tab bar (Qt 5.6 compatible), pinned to the bottom of the page so
 * the labels stay clear of the device notch.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

import QtQuick 2.0
import Sailfish.Silica 1.0
import io.thp.pyotherside 1.5

Page {
    id: page

    property int currentTab: 0
    property bool loading: false
    property string lastGlobalRun: ""
    property string skipBanner: ""
    property bool anySafetyAbort: false
    property bool remoteLoaded: false
    property string remoteError: ""
    // Separate from "loading": that one is shared with the sync pair
    // refresh, which runs right before the remote listing when the page
    // becomes active again.
    property bool remoteLoading: false
    // Backend wording for the remote top level ({key, one, many} from Python).
    property var remoteTerms: ({})

    Terminology {
        id: terms
        source: page.remoteTerms
    }

    onStatusChanged: {
        if (status === PageStatus.Active) {
            python.refresh();
            if (currentTab === 1) {
                python.loadLibraries();
            }
        }
    }

    onCurrentTabChanged: {
        if (currentTab === 1 && !remoteLoaded) {
            python.loadLibraries();
        }
    }

    ListModel { id: pairsModel }
    ListModel { id: libsModel }

    // --- Tab bar -----------------------------------------------------------

    Row {
        id: tabBar
        anchors.bottom: parent.bottom
        width: parent.width
        height: Theme.itemSizeMedium
        z: 1

        Repeater {
            model: [qsTr("Local syncs"), qsTr("Remote")]

            delegate: BackgroundItem {
                width: tabBar.width / 2
                height: tabBar.height
                onClicked: page.currentTab = index

                Label {
                    anchors.centerIn: parent
                    text: modelData
                    font.pixelSize: Theme.fontSizeLarge
                    color: page.currentTab === index ? Theme.highlightColor
                                                     : Theme.secondaryColor
                }

                Rectangle {
                    anchors.top: parent.top
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: parent.width - 2 * Theme.paddingLarge
                    height: Math.round(Theme.paddingSmall / 2) + 1
                    radius: height / 2
                    color: Theme.highlightColor
                    visible: page.currentTab === index
                }
            }
        }
    }

    // Thin divider so list content scrolling up to the bar stays readable.
    Rectangle {
        anchors.bottom: tabBar.top
        width: parent.width
        height: 1
        z: 1
        color: Theme.secondaryColor
        opacity: 0.4
    }

    // --- Left tab: local syncs ----------------------------------------------

    SilicaListView {
        id: localView
        visible: page.currentTab === 0
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: tabBar.top
        clip: true
        model: pairsModel

        header: Column {
            width: localView.width

            Item { width: 1; height: Theme.itemSizeSmall }

            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * Theme.horizontalPageMargin
                visible: page.skipBanner.length > 0
                wrapMode: Text.WordWrap
                font.pixelSize: Theme.fontSizeSmall
                color: Theme.highlightColor
                text: qsTr("Last sync skipped: %1").arg(page.skipBanner)
            }

            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * Theme.horizontalPageMargin
                visible: page.anySafetyAbort
                wrapMode: Text.WordWrap
                font.pixelSize: Theme.fontSizeSmall
                color: "#ff9955"
                text: qsTr("Sync paused: unusually many changes - long-press the pair and choose 'Force sync' to confirm")
            }

            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * Theme.horizontalPageMargin
                visible: page.lastGlobalRun.length > 0
                font.pixelSize: Theme.fontSizeExtraSmall
                color: Theme.secondaryColor
                text: qsTr("Last full sync: %1").arg(page.lastGlobalRun)
            }

            Item { width: 1; height: Theme.paddingLarge }
        }

        PullDownMenu {
            MenuItem {
                text: qsTr("Settings")
                onClicked: pageStack.push(Qt.resolvedUrl("SettingsPage.qml"))
            }
            MenuItem {
                text: qsTr("New sync pair")
                onClicked: pageStack.push(Qt.resolvedUrl("SyncPairEditorPage.qml"))
            }
            MenuItem {
                text: qsTr("Sync now")
                enabled: pairsModel.count > 0
                onClicked: python.syncAll()
            }
        }

        ViewPlaceholder {
            enabled: pairsModel.count === 0 && !page.loading && page.currentTab === 0
            text: qsTr("No sync pairs yet")
            hintText: qsTr("Pull down to create one")
        }

        delegate: ListItem {
            id: listItem
            contentHeight: pairColumn.height + 2 * Theme.paddingMedium
            menu: contextMenu

            function requestDeletion() {
                remorseAction(qsTr("Deleting sync pair"), function() {
                    python.deletePair(pair_id);
                });
            }

            Image {
                id: typeIcon
                x: Theme.horizontalPageMargin
                anchors.verticalCenter: parent.verticalCenter
                source: pair_type === "folder" ? "image://theme/icon-m-folder"
                                               : "image://theme/icon-m-file-other"
            }

            Label {
                id: statusGlyph
                anchors.right: parent.right
                anchors.rightMargin: Theme.horizontalPageMargin
                anchors.verticalCenter: parent.verticalCenter
                font.pixelSize: Theme.fontSizeLarge
                text: running ? "⇄"
                    : (paused ? "⏸"
                    : (last_ok === "ok" ? "✓"
                    : (last_ok === "fail" ? "✗" : "•")))
                color: running ? Theme.highlightColor
                     : (paused ? "#ff9955"
                     : (last_ok === "ok" ? "#66cc66"
                     : (last_ok === "fail" ? "#ff6666" : Theme.secondaryColor)))
            }

            Column {
                id: pairColumn
                anchors.left: typeIcon.right
                anchors.leftMargin: Theme.paddingLarge
                anchors.right: statusGlyph.left
                anchors.rightMargin: Theme.paddingLarge
                anchors.verticalCenter: parent.verticalCenter

                Label {
                    width: parent.width
                    text: local_name
                    truncationMode: TruncationMode.Fade
                    color: listItem.highlighted ? Theme.highlightColor : Theme.primaryColor
                }

                Label {
                    width: parent.width
                    font.pixelSize: Theme.fontSizeSmall
                    color: Theme.secondaryColor
                    truncationMode: TruncationMode.Fade
                    text: remote_name + (last_run.length > 0 ? " - " + last_run : "")
                }

                Label {
                    width: parent.width
                    font.pixelSize: Theme.fontSizeExtraSmall
                    color: last_ok === "fail" ? "#ff6666" : "#ff9955"
                    wrapMode: Text.WordWrap
                    visible: message.length > 0
                    text: message
                }
            }

            Component {
                id: contextMenu
                ContextMenu {
                    MenuItem {
                        text: qsTr("Sync this pair")
                        enabled: !running
                        onClicked: python.syncPair(pair_id, false)
                    }
                    MenuItem {
                        visible: safety_abort
                        text: qsTr("Force sync (confirm big change)")
                        enabled: !running
                        onClicked: python.syncPair(pair_id, true)
                    }
                    MenuItem {
                        visible: paused && !safety_abort
                        text: qsTr("Resume")
                        onClicked: python.resumePair(pair_id)
                    }
                    MenuItem {
                        text: qsTr("Show log")
                        onClicked: pageStack.push(Qt.resolvedUrl("SyncLogPage.qml"),
                                                  { pairId: pair_id })
                    }
                    MenuItem {
                        text: qsTr("Edit")
                        onClicked: pageStack.push(Qt.resolvedUrl("SyncPairEditorPage.qml"),
                                                  { pairId: pair_id })
                    }
                    MenuItem {
                        text: qsTr("Delete")
                        onClicked: listItem.requestDeletion()
                    }
                }
            }
        }

        VerticalScrollDecorator { }
    }

    // --- Right tab: remote top level overview --------------------------------

    SilicaListView {
        id: remoteView
        visible: page.currentTab === 1
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: tabBar.top
        clip: true
        model: libsModel

        // Keeps the first entry clear of the status bar / notch area.
        header: Item { width: remoteView.width; height: Theme.itemSizeSmall }

        PullDownMenu {
            MenuItem {
                text: qsTr("Settings")
                onClicked: pageStack.push(Qt.resolvedUrl("SettingsPage.qml"))
            }
            MenuItem {
                text: terms.createTitle
                onClicked: {
                    var dialog = pageStack.push(newLibraryDialog);
                    dialog.accepted.connect(function() {
                        python.makeLibrary(dialog.libraryName);
                    });
                }
            }
            MenuItem {
                text: qsTr("Refresh")
                onClicked: python.loadLibraries()
            }
        }

        ViewPlaceholder {
            enabled: libsModel.count === 0 && !page.loading && page.currentTab === 1
            text: page.remoteError.length > 0 ? page.remoteError : terms.none
            hintText: page.remoteError.length > 0
                      ? qsTr("Check the account settings, then pull down to refresh")
                      : terms.createHint
        }

        delegate: BackgroundItem {
            id: libItem
            width: remoteView.width
            height: Theme.itemSizeMedium
            onClicked: pageStack.push(Qt.resolvedUrl("RemoteBrowserPage.qml"),
                                      { remotePath: path })

            Image {
                id: libIcon
                x: Theme.horizontalPageMargin
                anchors.verticalCenter: parent.verticalCenter
                source: encrypted ? "image://theme/icon-m-device-lock"
                                  : "image://theme/icon-m-folder"
            }

            Label {
                anchors.left: libIcon.right
                anchors.leftMargin: Theme.paddingLarge
                anchors.right: parent.right
                anchors.rightMargin: Theme.horizontalPageMargin
                anchors.verticalCenter: parent.verticalCenter
                text: name
                truncationMode: TruncationMode.Fade
                color: libItem.highlighted ? Theme.highlightColor : Theme.primaryColor
            }
        }

        VerticalScrollDecorator { }
    }

    BusyIndicator {
        anchors.centerIn: parent
        size: BusyIndicatorSize.Large
        running: page.loading && ((page.currentTab === 0 && pairsModel.count === 0)
                                  || (page.currentTab === 1 && libsModel.count === 0))
    }

    Component {
        id: newLibraryDialog
        Dialog {
            property string libraryName: nameField.text

            Column {
                width: parent.width
                DialogHeader { title: terms.createTitle }
                TextField {
                    id: nameField
                    width: parent.width
                    label: qsTr("Name")
                    placeholderText: qsTr("Name")
                    focus: true
                    EnterKey.iconSource: "image://theme/icon-m-enter-accept"
                    EnterKey.onClicked: accept()
                }
            }
        }
    }

    // Pick up results of background (timer) runs while the page is visible.
    Timer {
        interval: 30000
        repeat: true
        running: page.status === PageStatus.Active
        onTriggered: python.refresh()
    }

    Python {
        id: python

        function baseName(path) {
            var parts = path.replace(/\/+$/, "").split("/");
            return parts.length > 0 ? parts[parts.length - 1] : path;
        }

        function refresh() {
            page.loading = true;
            call('sync_pairs.get_store', [], function(store) {
                page.loading = false;
                page.lastGlobalRun = store.last_global_run || "";
                page.skipBanner = store.last_skip || "";
                var abort = false;
                pairsModel.clear();
                for (var i = 0; i < store.pairs.length; i++) {
                    var pair = store.pairs[i];
                    if (pair.safety_abort) {
                        abort = true;
                    }
                    pairsModel.append({
                        pair_id: pair.id,
                        pair_type: pair.type,
                        local_name: baseName(pair.local),
                        remote_name: baseName(pair.remote),
                        last_run: pair.last_run || "",
                        message: (pair.last_message && pair.last_message !== "OK")
                                 ? pair.last_message
                                 : (pair.paused ? qsTr("Paused - long-press to resume") : ""),
                        paused: !!pair.paused,
                        safety_abort: !!pair.safety_abort,
                        running: false,
                        last_ok: pair.last_ok === true ? "ok"
                               : (pair.last_ok === false ? "fail" : "never")
                    });
                }
                page.anySafetyAbort = abort;
            });
        }

        function loadLibraries() {
            if (page.remoteLoading) {
                // A listing is already on its way - a second one would only
                // repeat the same remote round trip.
                return;
            }
            page.remoteLoading = true;
            page.loading = true;
            page.remoteError = "";
            call('remote_browser.list_dir', [""], function(result) {
                page.remoteLoading = false;
                page.loading = false;
                page.remoteLoaded = true;
                libsModel.clear();
                if (result.ok) {
                    for (var i = 0; i < result.entries.length; i++) {
                        if (result.entries[i].is_dir) {
                            libsModel.append(result.entries[i]);
                        }
                    }
                } else {
                    page.remoteError = result.message;
                }
            });
        }

        function makeLibrary(name) {
            call('remote_browser.make_dir', ["", name], function(result) {
                Notices.show(result.ok ? terms.created : result.message);
                loadLibraries();
            });
        }

        function findIndex(pairId) {
            for (var i = 0; i < pairsModel.count; i++) {
                if (pairsModel.get(i).pair_id === pairId) {
                    return i;
                }
            }
            return -1;
        }

        function syncAll() {
            Notices.show(qsTr("Synchronization started"));
            call('sync_engine.run_all_async', [], function() {});
        }

        function syncPair(pairId, force) {
            call('sync_engine.run_pair_async', [pairId, force], function() {});
        }

        function resumePair(pairId) {
            call('sync_pairs.update_pair', [pairId, { paused: false }], function() {
                refresh();
            });
        }

        function deletePair(pairId) {
            call('sync_pairs.delete_pair', [pairId], function() {
                Notices.show(qsTr("Sync pair deleted"));
                refresh();
            });
        }

        function loadTerms() {
            call('config_manager.get_account_summary', [], function(summary) {
                page.remoteTerms = (summary && summary.terms) ? summary.terms : ({});
            });
        }

        function checkFirstRun() {
            // First-start wizard: open the account dialog
            // directly when no account is configured yet.
            call('config_manager.get_account_summary', [], function(summary) {
                page.remoteTerms = (summary && summary.terms) ? summary.terms : ({});
                if (!summary && pageStack.depth === 1) {
                    Notices.show(qsTr("Welcome! Please set up your account first."));
                    pageStack.push(Qt.resolvedUrl("AccountPage.qml"));
                }
            });
        }

        Component.onCompleted: {
            addImportPath(Qt.resolvedUrl('../utilities'));

            setHandler('sync-status', function(info) {
                var index = findIndex(info.pair);
                if (index >= 0) {
                    pairsModel.setProperty(index, "running", !!info.running);
                    if (!info.running) {
                        refresh();
                    }
                }
            });

            setHandler('sync-all-finished', function(info) {
                if (info.skipped) {
                    Notices.show(qsTr("Sync skipped: %1").arg(info.reason));
                } else {
                    Notices.show(qsTr("Synchronization finished"));
                }
                refresh();
            });

            setHandler('settings-applied', function(result) {
                Notices.show(result.message);
            });

            setHandler('account-result', function(result) {
                // The result is shown on the AccountTestPage; this page just
                // has to pick up the new account - including its wording,
                // which changes with the backend.
                loadTerms();
                page.remoteLoaded = false;
                if (page.currentTab === 1) {
                    python.loadLibraries();
                }
            });

            importModule('sync_pairs', function() {
                importModule('sync_engine', function() {
                    importModule('remote_browser', function() {
                        importModule('config_manager', function() {
                            refresh();
                            checkFirstRun();
                        });
                    });
                });
            });
        }

        onError: {
            console.log('[ferry] python error: ' + traceback);
            page.loading = false;
            page.remoteLoading = false;
        }
    }
}
