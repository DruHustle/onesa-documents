import XCTest

final class PublicGuideTests: XCTestCase {
    @MainActor func testTouchPublicSearchResult() throws {
        let safari = XCUIApplication(bundleIdentifier: "com.apple.mobilesafari")
        safari.activate()
        let web = safari.webViews.firstMatch
        XCTAssertTrue(web.waitForExistence(timeout: 45))
        let fields = web.textFields
        let named = fields.matching(NSPredicate(format: "label CONTAINS[c] %@", "Search")).firstMatch
        let input = named.waitForExistence(timeout: 20) ? named : fields.firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 15), "The public page did not expose its search entry")
        input.tap()
        input.typeText("quickstart")
        let result = web.links.matching(NSPredicate(format: "label CONTAINS[c] %@", "Developer quickstart")).firstMatch
        XCTAssertTrue(result.waitForExistence(timeout: 35), "The public search did not return the guide")
        print("PUBLIC_GUIDE_NATIVE_SEARCH_RESULT_FOUND")
        result.tap()
        let guide = web.descendants(matching: .any).matching(NSPredicate(
            format: "label CONTAINS[c] %@", "Docker-only application startup"
        )).firstMatch
        if !guide.waitForExistence(timeout: 12) {
            web.swipeUp()
        }
        XCTAssertTrue(guide.waitForExistence(timeout: 15), "The native tap did not expose the guide's Docker-only application startup section")
        print("PUBLIC_GUIDE_NATIVE_TAP_OPENED_GUIDE")
    }
}
