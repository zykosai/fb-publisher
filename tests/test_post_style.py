from bike_publisher.facebook import FacebookPublisher
from bike_publisher.post_style import style_description


def test_sales_layout_preserves_claims_and_moves_profile_ad_to_footer():
    description = ('⚠️ WIĘCEJ ROWERÓW ZNAJDZIESZ NA MOIM PROFILU! ⚠️\n⠀\n'
        'Na sprzedaż rower młodzieżowy Winora Rage na kołach 26 cali. Widoczne rysy.\n⠀\n'
        '⚙️ Przerzutka tylna: Shimano Tourney🔧 Korba: trzy tarcze\n'
        '📍 Odbiór: Środa Wielkopolska\n'
        'Możliwa wymiana z dopłatą.kontakt: +48 000000000')
    text = FacebookPublisher._build_text({'price': '550', 'description': description})
    assert text.startswith('Na sprzedaż rower młodzieżowy Winora Rage na kołach 26 cali. Widoczne rysy.')
    assert 'Widoczne rysy.' in text
    assert 'Shimano Tourney\n🔧 Korba: trzy tarcze' in text
    assert 'dopłatą.\n\nkontakt: +48 000000000' in text
    assert '⚠️ WIĘCEJ ROWERÓW ZNAJDZIESZ NA MOIM PROFILU! ⚠️' in text
    assert text.endswith('Cena: 550 zł')
    assert '⠀' not in text
    assert 'idealny' not in text


def test_unrecognized_language_and_long_sentences_are_not_rewritten():
    for body in ['Ready to ride. Contact: 123', 'Есть царапины. Без гарантии.', 'Na sprzedaż ' + 'a' * 150 + '.']:
        heading, text = style_description(body)
        assert heading == ''
        assert text == body


def test_sale_title_without_remaining_body_keeps_price():
    assert FacebookPublisher._build_text({'price': '400', 'description': 'Na sprzedaż rower miejski.'}) == 'Na sprzedaż rower miejski.\n\nCena: 400 zł'


def test_custom_heading_keeps_product_details_and_empty_removes_heading():
    job = {'price': '550', 'description': 'Na sprzedaż rower Winora Rage. Widoczne rysy.', 'post_heading': 'Winora dla Ciebie'}
    text = FacebookPublisher._build_text(job)
    assert text.startswith('Winora dla Ciebie\n\n')
    assert 'Na sprzedaż rower Winora Rage.' in text
    assert 'Widoczne rysy.' in text
    job['post_heading'] = ''
    assert FacebookPublisher._build_text(job) == 'Na sprzedaż rower Winora Rage. Widoczne rysy.\n\nCena: 550 zł'
    assert FacebookPublisher._build_text({'description':'Description only','post_heading':'  '}) == 'Description only'
    assert FacebookPublisher._build_text({'price':'','description':'','post_heading':''}) == ''
