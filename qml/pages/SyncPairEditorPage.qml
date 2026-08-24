/*
 * Ferry - sync pair editor.
 * Mode selection two-way/upload only, type selection folder/single file
 * (two-way only); local picker uses the own file browser, remote picker
 * uses the remote browser in picker mode.
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

    function pairMode() {
        return modeCombo.currentIndex === 1 ? "push" : "bisync";
    }

    function pairType() {
        // Uploading a single file makes no sense as a standing job, so the
        // type combo is hidden in that mode - keep the value in step with it.
        if (page.pairMode() !== "bisync") {
            return "folder";
        }
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
                id: modeCombo
                label: qsTr("Mode")
                menu: ContextMenu {
                    MenuItem { text: qsTr("Two-way sync") }
                    MenuItem { text: qsTr("Upload only (one-way)") }
                }
                onCurrentIndexChanged: {
                    if (page.pairMode() !== "bisync" && typeCombo.currentIndex === 1) {
                        // Drops back to "folder" and clears the picked file
                        // through the type combo's own handler.
                        typeCombo.currentIndex = 0;
                    }
                }
            }

            ComboBox {
                id: typeCombo
                label: qsTr("Type")
                visible: page.pairMode() === "bisync"
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
                text: page.pairMode() === "bisync"
                    ? qsTr("The first synchronization runs a full resync between both sides. Note: changes that keep a file's size identical are not detected (size-only comparison).")
                    : qsTr("New and changed files are uploaded to the remote folder. Ferry never deletes anything on the remote side, and remote changes are never copied back.")
            }

            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * Theme.horizontalPageMargin
                wrapMode: Text.WordWrap
                font.pixelSize: Theme.fontSizeSmall
                color: Theme.secondaryHighlightColor
                text: qsTr("Note: If you try to sync to an encrypted library (e.g. Seafile), the library must be unlocked first under remote tab.")
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
                     [page.pairType(), page.localPath, page.remotePath,
                      page.pairMode()],
                     function() {
                    page.busy = false;
                    Notices.show(qsTr("Sync pair created"));
                    pageStack.pop();
                });
            } else {
                call('sync_pairs.update_pair',
                     [page.pairId, { type: page.pairType(),
                                     mode: page.pairMode(),
                                     local: page.localPath,
                                     remote: page.remotePath,
                                     // A changed mode invalidates the stored
                                     // bisync listings, so always start over.
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
                            // Mode first: it may reset the type combo, which
                            // in turn clears the path set below.
                            modeCombo.currentIndex = pair.mode === "push" ? 1 : 0;
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
