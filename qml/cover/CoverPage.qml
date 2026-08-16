/*
 * Ferry - cover: sync status + "sync now" action.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

import QtQuick 2.0
import Sailfish.Silica 1.0
import io.thp.pyotherside 1.5

CoverBackground {
    id: cover

    property int pairCount: 0
    property int failCount: 0
    property string lastRun: ""
    property bool syncing: false

    onStatusChanged: {
        if (status === Cover.Active) {
            python.refresh();
        }
    }

    Column {
        anchors.centerIn: parent
        width: parent.width - 2 * Theme.paddingLarge
        spacing: Theme.paddingSmall

        Label {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Ferry 0.7"
            color: Theme.primaryColor
            font.pixelSize: Theme.fontSizeMedium
        }

        Label {
            anchors.horizontalCenter: parent.horizontalCenter
            text: cover.syncing ? qsTr("Syncing...")
                : (cover.pairCount === 0 ? qsTr("No sync pairs")
                : (cover.failCount > 0 ? qsTr("%1 failed").arg(cover.failCount)
                                       : qsTr("%1 pairs OK").arg(cover.pairCount)))
            color: cover.failCount > 0 ? "#ff6666" : Theme.secondaryHighlightColor
            font.pixelSize: Theme.fontSizeSmall
        }

        Label {
            anchors.horizontalCenter: parent.horizontalCenter
            visible: cover.lastRun.length > 0
            text: cover.lastRun
            color: Theme.secondaryColor
            font.pixelSize: Theme.fontSizeExtraSmall
        }
    }

    CoverActionList {
        enabled: cover.pairCount > 0 && !cover.syncing

        CoverAction {
            iconSource: "image://theme/icon-cover-sync"
            onTriggered: {
                cover.syncing = true;
                python.call('sync_engine.run_all_async', [], function() {});
            }
        }
    }

    Python {
        id: python

        function refresh() {
            call('sync_pairs.get_store', [], function(store) {
                cover.pairCount = store.pairs.length;
                cover.lastRun = store.last_global_run || "";
                var fails = 0;
                for (var i = 0; i < store.pairs.length; i++) {
                    if (store.pairs[i].last_ok === false) {
                        fails++;
                    }
                }
                cover.failCount = fails;
            });
        }

        Component.onCompleted: {
            addImportPath(Qt.resolvedUrl('../utilities'));

            setHandler('sync-all-finished', function(info) {
                cover.syncing = false;
                refresh();
            });

            importModule('sync_pairs', function() {
                importModule('sync_engine', function() {
                    refresh();
                });
            });
        }

        onError: console.log('[ferry] cover python error: ' + traceback)
    }
}
