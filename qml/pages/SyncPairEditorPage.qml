/*
 * Ferry - sync pair editor.
 * Type selection folder/single file; local picker uses the own file
 * browser, remote picker uses the remote browser in picker mode.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

import QtQuick 2.0
import Sailfish.Silica 1.0
import io.thp.pyotherside 1.5

Page {
    id: page

    property string pairId: ""      // empty = create new pair
    property string localPath: ""
    property string remotePath: ""
    property bool busy: false

    function pairType() {
        return typeCombo.currentIndex === 1 ? "file" : "folder";
    }

    SilicaFlickable {
        anchors.fill: parent
        contentHeight: column.height

        PullDownMenu {
            busy: page.busy
            MenuItem {
                text: qsTr("Save")
                enabled: !page.busy && page.localPath !== "" && page.remotePath !== ""
                onClicked: python.save()
            }
        }

        Column {
            id: column
            width: page.width
            spacing: Theme.paddingSmall

            PageHeader {
                title: page.pairId === "" ? qsTr("New sync pair") : qsTr("Edit sync pair")
            }

            ComboBox {
                id: typeCombo
                label: qsTr("Type")
                menu: ContextMenu {
                    MenuItem { text: qsTr("Synchronize a folder") }
                    MenuItem { text: qsTr("Synchronize a single file") }
                }
                onCurrentIndexChanged: {
                    // The picked local path no longer matches the type.
                    page.localPath = "";
                }
            }

            ValueButton {
                label: page.pairType() === "file" ? qsTr("Local file") : qsTr("Local folder")
                value: page.localPath !== "" ? page.localPath : qsTr("Select")
                onClicked: {
                    pageStack.push(Qt.resolvedUrl("FileBrowserPage.qml"), {
                        mode: page.pairType(),
                        onSelected: function(paths) {
                            pageStack.pop(page);
                            page.localPath = paths[0];
                        }
                    });
                }
            }

            ValueButton {
                label: qsTr("Remote folder")
                value: page.remotePath !== "" ? page.remotePath : qsTr("Select")
                onClicked: {
                    pageStack.push(Qt.resolvedUrl("RemoteBrowserPage.qml"), {
                        pickerMode: true,
                        onPicked: function(path) {
                            pageStack.pop(page);
                            page.remotePath = path;
                        }
                    });
                }
            }

            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * Theme.horizontalPageMargin
                wrapMode: Text.WordWrap
                font.pixelSize: Theme.fontSizeSmall
                color: Theme.secondaryHighlightColor
                text: qsTr("The first synchronization runs a full resync between both sides. Note: changes that keep a file's size identical are not detected (size-only comparison).")
            }
        }

        VerticalScrollDecorator { }
    }

    Python {
        id: python

        function save() {
            page.busy = true;
            if (page.pairId === "") {
                call('sync_pairs.add_pair',
                     [page.pairType(), page.localPath, page.remotePath],
                     function() {
                    page.busy = false;
                    Notices.show(qsTr("Sync pair created"));
                    pageStack.pop();
                });
            } else {
                call('sync_pairs.update_pair',
                     [page.pairId, { type: page.pairType(),
                                     local: page.localPath,
                                     remote: page.remotePath,
                                     needs_resync: true }],
                     function() {
                    page.busy = false;
                    Notices.show(qsTr("Sync pair updated"));
                    pageStack.pop();
                });
            }
        }

        Component.onCompleted: {
            addImportPath(Qt.resolvedUrl('../utilities'));
            importModule('sync_pairs', function() {
                if (page.pairId !== "") {
                    call('sync_pairs.get_pair', [page.pairId], function(pair) {
                        if (pair) {
                            typeCombo.currentIndex = pair.type === "file" ? 1 : 0;
                            page.localPath = pair.local;
                            page.remotePath = pair.remote;
                        }
                    });
                }
            });
        }

        onError: {
            console.log('[ferry] python error: ' + traceback);
            page.busy = false;
        }
    }
}
