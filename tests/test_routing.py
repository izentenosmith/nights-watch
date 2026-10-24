from nights_watch.catalog.domain import AlertRouter, LabelMatcher, WatchedApp


def app(name, **match):
    return WatchedApp(name, LabelMatcher(match))


SHOP = app("shop", namespace="shop")
SHOP_API = app("shop-api", namespace="shop", app="api")


def names(apps):
    return [a.name for a in apps]


def test_an_app_matches_when_all_its_labels_are_equal():
    router = AlertRouter([SHOP])
    assert names(router.candidates({"namespace": "shop", "severity": "critical"})) == ["shop"]


def test_an_app_does_not_match_when_one_label_differs_or_is_missing():
    router = AlertRouter([SHOP_API])
    assert router.candidates({"namespace": "shop", "app": "web"}) == []
    assert router.candidates({"namespace": "shop"}) == []


def test_no_app_matches():
    assert AlertRouter([SHOP]).candidates({"namespace": "blog"}) == []


def test_the_app_with_most_matchers_comes_first():
    router = AlertRouter([SHOP, SHOP_API])
    assert names(router.candidates({"namespace": "shop", "app": "api"})) == ["shop-api", "shop"]


def test_a_tie_goes_to_the_first_app_by_name():
    router = AlertRouter([app("beta", env="prod"), app("alpha", team="x")])
    assert names(router.candidates({"env": "prod", "team": "x"})) == ["alpha", "beta"]
