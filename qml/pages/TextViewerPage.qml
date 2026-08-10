/*
 * Ferry - plain text viewer for remote .txt files.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

import QtQuick 2.0
import Sailfish.Silica 1.0
import io.thp.pyotherside 1.5

Page {
    id: page

    property string remotePath: ""
    property string fileName: ""
    property bool loading: false
    property string errorMessage: ""

    onStatusChanged: {
        // Reload after returning from the editor.
        if (status === PageStatus.Active && !page.loading) {
            python.load();
        }
    }

    SilicaFlickable {
        anchors.fill: parent
        contentHeight: column.height

        PullDownMenu {
            MenuItem {
                text: qsTr("Edit")
                enabled: !page.loading && page.errorMessage.length === 0
                onClicked: pageStack.push(Qt.resolvedUrl("TextEditorPage.qml"),
                                          { remotePath: page.remotePath,
                                            fileName: page.fileName })
            }
        }

        Column {
            id: column
            width: page.width

            PageHeader { title: page.fileName }

            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * Theme.horizontalPageMargin
                visible: page.errorMessage.length > 0
                wrapMode: Text.WordWrap
                color: "#ff6666"
                text: page.errorMessage
            }

            Label {
                id: contentLabel
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * Theme.horizontalPageMargin
                visible: page.errorMessage.length === 0
                wrapMode: Text.WrapAnywhere
                font.pixelSize: Theme.fontSizeSmall
                font.family: "monospace"
                color: Theme.primaryColor
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
            page.errorMessage = "";
            call('remote_browser.read_text_file', [page.remotePath], function(result) {
                page.loading = false;
                if (result.ok) {
                    contentLabel.text = result.content;
                } else {
                    page.errorMessage = result.message;
                }
            });
        }

        Component.onCompleted: {
            addImportPath(Qt.resolvedUrl('../utilities'));
            importModule('remote_browser', function() {
                load();
            });
        }

        onError: {
            console.log('[ferry] python error: ' + traceback);
            page.loading = false;
            page.errorMessage = qsTr("Internal error - see log");
        }
    }
}
