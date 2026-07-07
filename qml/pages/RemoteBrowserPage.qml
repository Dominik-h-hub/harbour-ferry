/*
 * Sailfile - remote browser (FR-05, FR-07, FR-08, FR-09).
 * Lists libraries at the root and navigates folders via the page stack.
 * Downloads go to ~/Downloads; deletion is guarded by a remorse timer.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

import QtQuick 2.0
import Sailfish.Silica 1.0
import io.thp.pyotherside 1.5

Page {
    id: page

    property string remotePath: ""
    property bool loading: false
    property string errorMessage: ""
    property int activeTransferId: -1
    property string transferLabel: ""
    property int transferPercent: 0

    // Picker mode: used by the sync pair editor to choose a remote folder.
    property bool pickerMode: false
    property var onPicked: null

    function isTextFile(name) {
        return /\.txt$/i.test(name);
    }

    function isImageFile(name) {
        return /\.(jpe?g|png|gif|bmp|webp)$/i.test(name);
    }

    ListModel { id: entriesModel }

    function pageTitle() {
        if (remotePath === "")
            return qsTr("Libraries");
        var parts = remotePath.split("/");
        return parts[parts.length - 1];
    }

    SilicaListView {
        id: listView
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: transferPanel.visible ? transferPanel.top : parent.bottom
        model: entriesModel

        header: PageHeader {
            title: page.pageTitle()
            description: page.remotePath === "" ? qsTr("Remote storage") : page.remotePath
        }

        PullDownMenu {
            busy: page.loading
            MenuItem {
                visible: page.pickerMode && page.remotePath !== ""
                text: qsTr("Select this folder")
                onClicked: {
                    if (page.onPicked) {
                        page.onPicked(page.remotePath);
                    }
                }
            }
            MenuItem {
                visible: !page.pickerMode
                text: page.remotePath === "" ? qsTr("New library") : qsTr("New folder")
                enabled: !page.loading
                onClicked: {
                    var dialog = pageStack.push(newFolderDialog);
                    dialog.accepted.connect(function() {
                        python.makeDir(dialog.folderName);
                    });
                }
            }
            MenuItem {
                // Files live inside libraries, so no upload at root level.
                visible: !page.pickerMode && page.remotePath !== ""
                text: qsTr("Upload here")
                enabled: !page.loading && page.activeTransferId < 0
                onClicked: {
                    var store = { paths: [] };
                    pageStack.push(Qt.resolvedUrl("FileBrowserPage.qml"), {
                        mode: "files",
                        selectionStore: store,
                        onSelected: function(paths) {
                            pageStack.pop(page);
                            python.upload(paths);
                        }
                    });
                }
            }
            MenuItem {
                text: qsTr("Refresh")
                enabled: !page.loading
                onClicked: python.reload()
            }
        }

        ViewPlaceholder {
            enabled: entriesModel.count === 0 && !page.loading
            text: page.errorMessage.length > 0 ? page.errorMessage
                                               : qsTr("Empty folder")
            hintText: page.errorMessage.length > 0
                      ? qsTr("Check the account settings, then pull down to refresh")
                      : qsTr("Pull down to create a folder")
        }

        delegate: ListItem {
            id: listItem
            contentHeight: Theme.itemSizeMedium
            menu: page.pickerMode ? null : contextMenu

            function requestDeletion() {
                remorseAction(qsTr("Deleting %1").arg(name), function() {
                    python.deleteEntry(path, is_dir);
                });
            }

            Image {
                id: entryIcon
                x: Theme.horizontalPageMargin
                anchors.verticalCenter: parent.verticalCenter
                source: is_dir ? (encrypted ? "image://theme/icon-m-device-lock"
                                            : "image://theme/icon-m-folder")
                               : "image://theme/icon-m-file-other"
            }

            Label {
                id: nameLabel
                anchors.left: entryIcon.right
                anchors.leftMargin: Theme.paddingLarge
                anchors.right: parent.right
                anchors.rightMargin: Theme.horizontalPageMargin
                anchors.verticalCenter: parent.verticalCenter
                anchors.verticalCenterOffset: is_dir ? 0 : -Theme.paddingMedium
                text: name
                truncationMode: TruncationMode.Fade
                color: listItem.highlighted ? Theme.highlightColor : Theme.primaryColor
            }

            Label {
                visible: !is_dir
                anchors.left: nameLabel.left
                anchors.top: nameLabel.bottom
                font.pixelSize: Theme.fontSizeExtraSmall
                color: Theme.secondaryColor
                text: (size >= 0 ? Format.formatFileSize(size) + "  " : "") + mtime
            }

            onClicked: {
                if (is_dir) {
                    pageStack.push(Qt.resolvedUrl("RemoteBrowserPage.qml"),
                                   { remotePath: path,
                                     pickerMode: page.pickerMode,
                                     onPicked: page.onPicked });
                } else if (!page.pickerMode) {
                    openMenu();
                }
            }

            Component {
                id: contextMenu
                ContextMenu {
                    MenuItem {
                        visible: !is_dir && (page.isTextFile(name) || page.isImageFile(name))
                        text: qsTr("View")
                        onClicked: {
                            if (page.isTextFile(name)) {
                                pageStack.push(Qt.resolvedUrl("TextViewerPage.qml"),
                                               { remotePath: path, fileName: name });
                            } else {
                                pageStack.push(Qt.resolvedUrl("ImageViewerPage.qml"),
                                               { remotePath: path, fileName: name });
                            }
                        }
                    }
                    MenuItem {
                        visible: !is_dir && page.isTextFile(name)
                        text: qsTr("Edit")
                        onClicked: pageStack.push(Qt.resolvedUrl("TextEditorPage.qml"),
                                                  { remotePath: path, fileName: name })
                    }
                    MenuItem {
                        text: qsTr("Download")
                        onClicked: python.download(path, name, is_dir)
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

    BusyIndicator {
        anchors.centerIn: parent
        size: BusyIndicatorSize.Large
        running: page.loading && entriesModel.count === 0
    }

    // Active download panel (FR-09: progress + cancel).
    Rectangle {
        id: transferPanel
        visible: page.activeTransferId >= 0
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: Theme.itemSizeLarge
        color: Theme.rgba(Theme.highlightBackgroundColor, 0.2)

        ProgressBar {
            id: transferProgress
            anchors.left: parent.left
            anchors.right: cancelButton.left
            anchors.verticalCenter: parent.verticalCenter
            minimumValue: 0
            maximumValue: 100
            value: page.transferPercent
            label: page.transferLabel
        }

        IconButton {
            id: cancelButton
            anchors.right: parent.right
            anchors.rightMargin: Theme.paddingMedium
            anchors.verticalCenter: parent.verticalCenter
            icon.source: "image://theme/icon-m-clear"
            onClicked: python.cancelTransfer()
        }
    }

    Component {
        id: libraryPasswordDialog
        Dialog {
            property string library: ""
            property string libraryPassword: passwordField.text

            Column {
                width: parent.width
                DialogHeader {
                    acceptText: qsTr("Unlock")
                    title: qsTr("Encrypted library")
                }
                Label {
                    x: Theme.horizontalPageMargin
                    width: parent.width - 2 * Theme.horizontalPageMargin
                    wrapMode: Text.WordWrap
                    font.pixelSize: Theme.fontSizeSmall
                    color: Theme.secondaryHighlightColor
                    text: qsTr("Enter the password for '%1'. It is stored securely for future access.").arg(library)
                }
                PasswordField {
                    id: passwordField
                    width: parent.width
                    label: qsTr("Library password")
                    focus: true
                    EnterKey.iconSource: "image://theme/icon-m-enter-accept"
                    EnterKey.onClicked: accept()
                }
            }
        }
    }

    Component {
        id: newFolderDialog
        Dialog {
            property string folderName: nameField.text

            Column {
                width: parent.width
                DialogHeader {
                    title: page.remotePath === "" ? qsTr("New library") : qsTr("New folder")
                }
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

    Python {
        id: python

        function reload() {
            page.loading = true;
            page.errorMessage = "";
            call('remote_browser.list_dir', [page.remotePath], function(result) {
                page.loading = false;
                entriesModel.clear();
                if (result.ok) {
                    for (var i = 0; i < result.entries.length; i++) {
                        entriesModel.append(result.entries[i]);
                    }
                } else if (result.encrypted) {
                    askLibraryPassword(result.library);
                } else {
                    page.errorMessage = result.message;
                }
            });
        }

        function askLibraryPassword(library) {
            page.errorMessage = qsTr("Library is encrypted");
            var dialog = pageStack.push(libraryPasswordDialog, { library: library });
            dialog.accepted.connect(function() {
                page.loading = true;
                call('remote_browser.unlock_library',
                     [library, dialog.libraryPassword], function(result) {
                    Notices.show(result.message);
                    if (result.ok) {
                        reload();
                    } else {
                        page.loading = false;
                        page.errorMessage = result.message;
                    }
                });
            });
        }

        function makeDir(name) {
            page.loading = true;
            call('remote_browser.make_dir', [page.remotePath, name], function(result) {
                Notices.show(result.ok ? qsTr("Folder created") : result.message);
                if (!result.ok) {
                    page.loading = false;
                } else {
                    reload();
                }
            });
        }

        function deleteEntry(path, isDir) {
            call('remote_browser.delete_entry', [path, isDir], function(result) {
                Notices.show(result.ok ? qsTr("Deleted") : result.message);
                reload();
            });
        }

        function upload(paths) {
            call('remote_browser.upload', [paths, page.remotePath], function(result) {
                if (result.ok) {
                    page.activeTransferId = result.id;
                    page.transferPercent = 0;
                    page.transferLabel = qsTr("Uploading %1 file(s)").arg(paths.length);
                } else {
                    Notices.show(result.message);
                }
            });
        }

        function download(path, name, isDir) {
            call('remote_browser.download', [path, name, isDir], function(result) {
                if (result.ok) {
                    page.activeTransferId = result.id;
                    page.transferPercent = 0;
                    page.transferLabel = qsTr("Downloading %1").arg(name);
                } else {
                    page.errorMessage = result.message;
                }
            });
        }

        function cancelTransfer() {
            if (page.activeTransferId >= 0) {
                call('remote_browser.cancel_transfer', [page.activeTransferId],
                     function() {});
            }
        }

        Component.onCompleted: {
            addImportPath(Qt.resolvedUrl('../utilities'));

            setHandler('transfer-progress', function(info) {
                if (info.id === page.activeTransferId) {
                    page.transferPercent = info.percent;
                    if (info.info) {
                        page.transferLabel = info.info;
                    }
                }
            });

            setHandler('transfer-finished', function(info) {
                if (info.id === page.activeTransferId) {
                    page.activeTransferId = -1;
                    page.transferLabel = "";
                    page.transferPercent = 0;
                    // In-app banner feedback (no system notification, FR-20).
                    Notices.show(info.message);
                    reload();
                }
            });

            importModule('remote_browser', function() {
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
