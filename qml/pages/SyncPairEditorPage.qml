/*
 * Ferry - sync pair editor.
 * Optional display name, mode selection two-way/upload only/download only,
 * type selection folder/single file (two-way only); local picker uses the own file browser, remote picker
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
    // What the edited pair looked like when it was loaded (sync-relevant
    // fields only), to tell a rename apart from a real change.
    property var loadedPair: null

    // Mode combo entries in order; unknown modes load as two-way, as in
    // sync_pairs.pair_mode().
    readonly property var modes: ["bisync", "push", "pull"]

    function pairMode() {
        return page.modes[modeCombo.currentIndex] || "bisync";
    }

    function pairType() {
        // A single file as a standing one-way job makes no sense, so the
        // type combo is hidden in those modes - keep the value in step with it.
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

            TextField {
                id: nameField
                width: parent.width
                label: qsTr("Name (optional)")
                // The automatic name shows what an empty field means.
                placeholderText: page.localPath !== ""
                                 ? page.localPath.replace(/\/+$/, "").split("/").pop()
                                 : qsTr("Name (optional)")
                EnterKey.iconSource: "image://theme/icon-m-enter-close"
                EnterKey.onClicked: focus = false
            }

            ComboBox {
                id: modeCombo
                label: qsTr("Mode")
                menu: ContextMenu {
                    MenuItem { text: qsTr("Two-way sync") }
                    MenuItem { text: qsTr("Upload only (one-way)") }
                    MenuItem { text: qsTr("Download only (one-way)") }
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
                    : page.pairMode() === "push"
                    ? qsTr("New and changed files are uploaded to the remote folder. Ferry never deletes anything on the remote side, and remote changes are never copied back.")
                    : qsTr("New and changed files are downloaded from the remote folder. Ferry never deletes anything on the device, and local changes are never uploaded - a local file that differs from the remote one is overwritten with the remote version.")
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
                      page.pairMode(), nameField.text.trim()],
                     function() {
                    page.busy = false;
                    Notices.show(qsTr("Sync pair created"));
                    pageStack.pop();
                });
            } else {
                var fields = { name: nameField.text.trim(),
                               type: page.pairType(),
                               mode: page.pairMode(),
                               local: page.localPath,
                               remote: page.remotePath };
                var loaded = page.loadedPair;
                if (!loaded || loaded.type !== fields.type
                        || loaded.mode !== fields.mode
                        || loaded.local !== fields.local
                        || loaded.remote !== fields.remote) {
                    // A changed mode or path invalidates the stored bisync
                    // listings, so start over. A new name alone does not.
                    fields.needs_resync = true;
                }
                call('sync_pairs.update_pair', [page.pairId, fields],
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
                            modeCombo.currentIndex = Math.max(0, page.modes.indexOf(pair.mode));
                            typeCombo.currentIndex = pair.type === "file" ? 1 : 0;
                            page.localPath = pair.local;
                            page.remotePath = pair.remote;
                            nameField.text = pair.name || "";
                            page.loadedPair = { type: page.pairType(),
                                                mode: page.pairMode(),
                                                local: pair.local,
                                                remote: pair.remote };
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
