/*
 * Ferry - diagnostics page.
 * Runs the validation spike tests via the Python diagnostics module and
 * shows PASS/FAIL per test. Full details go to stdout/journal and to a
 * report file in the app data directory.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

import QtQuick 2.0
import Sailfish.Silica 1.0
import io.thp.pyotherside 1.5

Page {
    id: page

    property bool running: false
    property string summary: ""
    property string reportPath: ""

    ListModel { id: testModel }

    SilicaListView {
        id: listView
        anchors.fill: parent
        model: testModel

        header: Column {
            width: listView.width

            PageHeader {
                title: qsTr("Diagnostics")
                description: qsTr("TS-00 validation tests")
            }

            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * Theme.horizontalPageMargin
                wrapMode: Text.WordWrap
                color: Theme.highlightColor
                text: page.running ? qsTr("Tests running...") : page.summary
            }

            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * Theme.horizontalPageMargin
                wrapMode: Text.WrapAnywhere
                visible: page.reportPath.length > 0
                font.pixelSize: Theme.fontSizeExtraSmall
                color: Theme.secondaryColor
                text: qsTr("Report: ") + page.reportPath
            }

            Item { width: 1; height: Theme.paddingLarge }
        }

        PullDownMenu {
            busy: page.running
            MenuItem {
                text: qsTr("Run all tests")
                enabled: !page.running
                onClicked: python.runAll()
            }
        }

        ViewPlaceholder {
            enabled: testModel.count === 0 && !page.running
            text: qsTr("No results yet")
            hintText: qsTr("Pull down to run all tests")
        }

        delegate: Item {
            width: listView.width
            height: contentColumn.height + Theme.paddingLarge

            Column {
                id: contentColumn
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * Theme.horizontalPageMargin
                spacing: Theme.paddingSmall

                Row {
                    spacing: Theme.paddingMedium

                    Label {
                        text: status
                        font.bold: true
                        color: status === "PASS" ? "#66cc66"
                             : status === "FAIL" ? "#ff6666"
                             : Theme.highlightColor
                    }

                    Label {
                        text: name
                        color: Theme.primaryColor
                    }
                }

                Label {
                    width: parent.width
                    visible: details.length > 0
                    text: details
                    wrapMode: Text.WrapAnywhere
                    font.pixelSize: Theme.fontSizeExtraSmall
                    color: Theme.secondaryColor
                }
            }
        }

        VerticalScrollDecorator { }
    }

    BusyIndicator {
        anchors.centerIn: parent
        size: BusyIndicatorSize.Large
        running: page.running && testModel.count === 0
    }

    Python {
        id: python

        // Probe QML plugin availability
        // results are passed into the Python report.
        function qmlProbes() {
            var mods = ["Sailfish.Secrets 1.0", "Nemo.DBus 2.0",
                        "Nemo.Notifications 1.0", "Nemo.Configuration 1.0"];
            var results = [];
            for (var i = 0; i < mods.length; i++) {
                try {
                    var obj = Qt.createQmlObject(
                        "import QtQuick 2.0; import " + mods[i] + "; QtObject {}",
                        page, "qmlProbe");
                    results.push(mods[i] + ": OK");
                    obj.destroy();
                } catch (e) {
                    results.push(mods[i] + ": FAILED (" + String(e.message || e) + ")");
                }
            }
            return results.join("\n");
        }

        function runAll() {
            page.running = true;
            page.summary = "";
            page.reportPath = "";
            testModel.clear();
            call('diagnostics.run_all', [qmlProbes()], function() {});
        }

        Component.onCompleted: {
            addImportPath(Qt.resolvedUrl('../utilities'));

            setHandler('test-started', function(t) {
                testModel.append({name: t.name, status: "RUN", details: ""});
            });

            setHandler('test-result', function(t) {
                for (var i = 0; i < testModel.count; i++) {
                    if (testModel.get(i).name === t.name) {
                        testModel.set(i, {name: t.name, status: t.status, details: t.details});
                        return;
                    }
                }
                testModel.append({name: t.name, status: t.status, details: t.details});
            });

            setHandler('finished', function(summary, reportPath) {
                page.running = false;
                page.summary = summary;
                page.reportPath = reportPath;
            });

            importModule('diagnostics', function () {
                // Auto-run once when the page is opened the first time.
                runAll();
            });
        }

        onError: {
            console.log('[ferry] python error: ' + traceback);
            page.running = false;
            page.summary = qsTr("Python error - see log");
        }

        onReceived: {
            console.log('[ferry] message from python: ' + data);
        }
    }
}
