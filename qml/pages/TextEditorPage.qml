/*
 * Ferry - plain text editor for remote .txt files.
 * Save via the pull-down menu; the page stays open while saving.
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
    property bool busy: false
    property string errorMessage: ""

    SilicaFlickable {
        anchors.fill: parent
        contentHeight: column.height

        PullDownMenu {
            busy: page.busy
            MenuItem {
                text: qsTr("Save")
                enabled: !page.busy
                onClicked: python.save()
            }
        }

        Column {
            id: column
            width: page.width

            PageHeader {
                title: page.fileName
                description: qsTr("Edit")
            }

            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * Theme.horizontalPageMargin
                visible: page.errorMessage.length > 0
                wrapMode: Text.WordWrap
                color: "#ff6666"
                text: page.errorMessage
            }

            TextArea {
                id: editor
                width: parent.width
                font.pixelSize: Theme.fontSizeSmall
                font.family: "monospace"
                label: page.fileName
                placeholderText: qsTr("File content")
            }

            Item { width: 1; height: Theme.paddingLarge }
        }

        VerticalScrollDecorator { }
    }

    BusyIndicator {
        anchors.centerIn: parent
        size: BusyIndicatorSize.Large
        running: page.busy
    }

    Python {
        id: python

        function save() {
            page.busy = true;
            page.errorMessage = "";
            call('remote_browser.save_text_file', [page.remotePath, editor.text],
                 function(result) {
                page.busy = false;
                Notices.show(result.ok ? qsTr("Saved") : result.message);
                if (result.ok) {
                    pageStack.pop();
                } else {
                    page.errorMessage = result.message;
                }
            });
        }

        Component.onCompleted: {
            addImportPath(Qt.resolvedUrl('../utilities'));
            page.busy = true;
            importModule('remote_browser', function() {
                call('remote_browser.read_text_file', [page.remotePath],
                     function(result) {
                    page.busy = false;
                    if (result.ok) {
                        editor.text = result.content;
                    } else {
                        page.errorMessage = result.message;
                    }
                });
            });
        }

        onError: {
            console.log('[ferry] python error: ' + traceback);
            page.busy = false;
            page.errorMessage = qsTr("Internal error - see log");
        }
    }
}
