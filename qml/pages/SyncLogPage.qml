/*
 * Ferry - sync log viewer (NFR-04).
 *
 * SPDX-License-Identifier: Apache-2.0
 */

import QtQuick 2.0
import Sailfish.Silica 1.0
import io.thp.pyotherside 1.5

Page {
    id: page

    property string pairId: ""
    property bool loading: false

    SilicaFlickable {
        anchors.fill: parent
        contentHeight: column.height

        PullDownMenu {
            MenuItem {
                text: qsTr("Refresh")
                onClicked: python.load()
            }
        }

        Column {
            id: column
            width: page.width

            PageHeader {
                title: qsTr("Sync log")
            }

            Label {
                id: logLabel
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * Theme.horizontalPageMargin
                wrapMode: Text.WrapAnywhere
                font.pixelSize: Theme.fontSizeTiny
                font.family: "monospace"
                color: Theme.secondaryColor
                text: ""
            }

            Item { width: 1; height: Theme.paddingLarge }
        }

        VerticalScrollDecorator { }
    }

    BusyIndicator {
        anchors.centerIn: parent
        size: BusyIndicatorSize.Large
        running: page.loading
    }

    Python {
        id: python

        function load() {
            page.loading = true;
            call('sync_engine.get_log', [page.pairId], function(content) {
                page.loading = false;
                logLabel.text = content;
            });
        }

        Component.onCompleted: {
            addImportPath(Qt.resolvedUrl('../utilities'));
            importModule('sync_engine', function() {
                load();
            });
        }

        onError: {
            console.log('[ferry] python error: ' + traceback);
            page.loading = false;
            logLabel.text = qsTr("Internal error - see log");
        }
    }
}
