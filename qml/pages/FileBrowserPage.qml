/*
 * Sailfile - own local file browser (FR-06, OD-08).
 * All file types are selectable; the visible area is restricted to the
 * standard user folders and removable media (overview at the root).
 *
 * Modes:
 *  - "files":  multi-selection of files (upload), accept via pulley
 *  - "file":   tapping a file selects it immediately (sync pair editor)
 *  - "folder": pulley "Select this folder" (sync pair editor)
 *
 * The opener passes `selectionStore` ({paths: []}, shared across levels)
 * and an `onSelected(paths)` callback.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

import QtQuick 2.0
import Sailfish.Silica 1.0
import io.thp.pyotherside 1.5

Page {
    id: page

    property string localPath: ""
    property string mode: "files"
    property var selectionStore: ({ paths: [] })
    property var onSelected: null
    property bool loading: false
    property string errorMessage: ""
    property int selCount: selectionStore.paths.length

    function refreshSelCount() {
        selCount = selectionStore.paths.length;
    }

    function toggleSelection(index) {
        var entry = entriesModel.get(index);
        var pos = selectionStore.paths.indexOf(entry.path);
        if (pos >= 0) {
            selectionStore.paths.splice(pos, 1);
            entriesModel.setProperty(index, "selected", false);
        } else {
            selectionStore.paths.push(entry.path);
            entriesModel.setProperty(index, "selected", true);
        }
        refreshSelCount();
    }

    onStatusChanged: {
        // Selection may have changed on a deeper level.
        if (status === PageStatus.Active) {
            refreshSelCount();
        }
    }

    ListModel { id: entriesModel }

    SilicaListView {
        id: listView
        anchors.fill: parent
        model: entriesModel

        header: PageHeader {
            title: page.localPath === "" ? qsTr("Select files")
                                         : page.localPath.split("/").pop()
            description: page.mode === "folder" ? qsTr("Choose a folder")
                       : (page.selCount > 0 ? qsTr("%1 selected").arg(page.selCount)
                                            : qsTr("Local files"))
        }

        PullDownMenu {
            visible: page.mode === "files" || (page.mode === "folder" && page.localPath !== "")
            MenuItem {
                visible: page.mode === "files"
                text: qsTr("Add selection (%1)").arg(page.selCount)
                enabled: page.selCount > 0
                onClicked: {
                    if (page.onSelected) {
                        page.onSelected(page.selectionStore.paths);
                    }
                }
            }
            MenuItem {
                visible: page.mode === "folder" && page.localPath !== ""
                text: qsTr("Select this folder")
                onClicked: {
                    if (page.onSelected) {
                        page.onSelected([page.localPath]);
                    }
                }
            }
        }

        ViewPlaceholder {
            enabled: entriesModel.count === 0 && !page.loading
            text: page.errorMessage.length > 0 ? page.errorMessage : qsTr("Empty folder")
        }

        delegate: ListItem {
            id: listItem
            contentHeight: Theme.itemSizeMedium

            Image {
                id: entryIcon
                x: Theme.horizontalPageMargin
                anchors.verticalCenter: parent.verticalCenter
                source: is_dir ? "image://theme/icon-m-folder"
                               : "image://theme/icon-m-file-other"
            }

            Label {
                id: nameLabel
                anchors.left: entryIcon.right
                anchors.leftMargin: Theme.paddingLarge
                anchors.right: checkIcon.visible ? checkIcon.left : parent.right
                anchors.rightMargin: Theme.horizontalPageMargin
                anchors.verticalCenter: parent.verticalCenter
                anchors.verticalCenterOffset: (!is_dir && size >= 0) ? -Theme.paddingMedium : 0
                text: name
                truncationMode: TruncationMode.Fade
                color: listItem.highlighted || selected ? Theme.highlightColor
                                                        : Theme.primaryColor
            }

            Label {
                visible: !is_dir && size >= 0
                anchors.left: nameLabel.left
                anchors.top: nameLabel.bottom
                font.pixelSize: Theme.fontSizeExtraSmall
                color: Theme.secondaryColor
                text: Format.formatFileSize(size) + "  " + mtime
            }

            Image {
                id: checkIcon
                visible: selected
                anchors.right: parent.right
                anchors.rightMargin: Theme.horizontalPageMargin
                anchors.verticalCenter: parent.verticalCenter
                source: "image://theme/icon-m-accept"
            }

            onClicked: {
                if (is_dir) {
                    pageStack.push(Qt.resolvedUrl("FileBrowserPage.qml"), {
                        localPath: path,
                        mode: page.mode,
                        selectionStore: page.selectionStore,
                        onSelected: page.onSelected
                    });
                } else if (page.mode === "file") {
                    if (page.onSelected) {
                        page.onSelected([path]);
                    }
                } else if (page.mode === "files") {
                    page.toggleSelection(index);
                }
            }
        }

        VerticalScrollDecorator { }
    }

    BusyIndicator {
        anchors.centerIn: parent
        size: BusyIndicatorSize.Large
        running: page.loading && entriesModel.count === 0
    }

    Python {
        id: python

        function reload() {
            page.loading = true;
            page.errorMessage = "";
            var handler = function(result) {
                page.loading = false;
                entriesModel.clear();
                if (result.ok) {
                    for (var i = 0; i < result.entries.length; i++) {
                        var entry = result.entries[i];
                        entry.selected =
                            page.selectionStore.paths.indexOf(entry.path) >= 0;
                        entriesModel.append(entry);
                    }
                } else {
                    page.errorMessage = result.message;
                }
            };
            if (page.localPath === "") {
                call('file_browser.list_roots', [], handler);
            } else {
                call('file_browser.list_dir', [page.localPath], handler);
            }
        }

        Component.onCompleted: {
            addImportPath(Qt.resolvedUrl('../utilities'));
            importModule('file_browser', function() {
                reload();
            });
        }

        onError: {
            console.log('[sailfile] python error: ' + traceback);
            page.loading = false;
            page.errorMessage = qsTr("Internal error - see log");
        }
    }
}
