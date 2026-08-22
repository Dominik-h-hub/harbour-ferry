/*
 * Ferry - image viewer for remote photos. The file is fetched into a
 * private cache and shown full screen.
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
    property string localPath: ""

    allowedOrientations: Orientation.All
    backgroundColor: "black"

    Rectangle {
        anchors.fill: parent
        color: "black"
    }

    Image {
        id: image
        anchors.fill: parent
        fillMode: Image.PreserveAspectFit
        asynchronous: true
        source: page.localPath !== "" ? "file://" + page.localPath : ""
        onStatusChanged: {
            if (status === Image.Error) {
                page.errorMessage = qsTr("Image could not be displayed");
            }
        }
    }

    PageHeader {
        title: page.fileName
        opacity: 0.7
    }

    Label {
        anchors.centerIn: parent
        width: parent.width - 2 * Theme.horizontalPageMargin
        visible: page.errorMessage.length > 0
        wrapMode: Text.WordWrap
        horizontalAlignment: Text.AlignHCenter
        color: "#ff6666"
        text: page.errorMessage
    }

    BusyIndicator {
        anchors.centerIn: parent
        size: BusyIndicatorSize.Large
        running: page.loading || image.status === Image.Loading
    }

    Python {
        id: python

        Component.onCompleted: {
            addImportPath(Qt.resolvedUrl('../utilities'));
            page.loading = true;
            importModule('remote_browser', function() {
                call('remote_browser.fetch_image', [page.remotePath, page.fileName],
                     function(result) {
                    page.loading = false;
                    if (result.ok) {
                        page.localPath = result.local_path;
                    } else {
                        page.errorMessage = result.message;
                    }
                });
            });
        }

        onError: {
            console.log('[ferry] python error: ' + traceback);
            page.loading = false;
            page.errorMessage = qsTr("Internal error - see log");
        }
    }
}
