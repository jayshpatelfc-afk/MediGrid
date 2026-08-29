import unittest

from app import app


class PrototypeApiTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_occupancy_uses_one_latest_snapshot(self):
        dashboard = self.client.get('/api/dashboard').get_json()
        beds = self.client.get('/api/bed-types').get_json()
        self.assertEqual(dashboard['reportingDate'], beds['reportingDate'])
        self.assertEqual(dashboard['occupiedBeds']['capacity'], beds['totalBeds'])
        self.assertEqual(dashboard['occupiedBeds']['value'], beds['occupiedBeds'])
        self.assertEqual(dashboard['occupiedBeds']['available'], beds['vacantBeds'])
        self.assertEqual(set(dashboard['trustScores']), {'Bed availability', 'Admissions', 'Discharges', 'Lab turnaround'})
        self.assertTrue(isinstance(dashboard['alerts'], list))
        self.assertTrue(dashboard['updatedAt'].startswith('2026-08-28'))

    def test_source_counts_match_imported_rows(self):
        sources = self.client.get('/api/sources').get_json()['sources']
        self.assertEqual([source['rows'] for source in sources], [549, 1087, 135])

    def test_live_day_data_covers_current_date(self):
        flow = self.client.get('/api/patient-flow?date=2026-08-28').get_json()
        self.assertEqual(flow['admissions'], 240)
        self.assertTrue(any(event['admission_at'].startswith('2026-08-28 23') for event in flow['events']))

    def test_patient_flow_date_filter(self):
        flow = self.client.get('/api/patient-flow?date=2026-07-30').get_json()
        self.assertEqual((flow['admissions'], flow['discharges'], flow['inCare']), (14, 8, 6))
        self.assertTrue(all(event['admission_at'].startswith('2026-07-30') or event['discharge_at'].startswith('2026-07-30') for event in flow['events']))

    def test_bottlenecks_report_flow_and_lab_signals(self):
        response = self.client.get('/api/bottlenecks?date=2026-07-30')
        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(data['bedAssignmentTimeAvailable'])
        self.assertTrue(data['departments'])
        self.assertTrue(all({'department', 'netFlow', 'labAverage', 'severity'} <= set(row) for row in data['departments']))

    def test_dashboard_can_switch_between_snapshot_dates(self):
        dates = app.config['DB'].execute('SELECT DISTINCT substr(snapshot_date, 1, 10) AS snapshot_date FROM bed_snapshots ORDER BY snapshot_date').fetchall()
        self.assertGreater(len(dates), 1)
        first = self.client.get(f"/api/dashboard?date={dates[0]['snapshot_date']}").get_json()
        second = self.client.get(f"/api/dashboard?date={dates[1]['snapshot_date']}").get_json()
        self.assertEqual(first['reportingDate'], dates[0]['snapshot_date'])
        self.assertEqual(second['reportingDate'], dates[1]['snapshot_date'])
        self.assertNotEqual(first['occupiedBeds']['available'], second['occupiedBeds']['available'])

    def test_trust_scores_follow_selected_snapshot_date(self):
        response = self.client.get('/api/trust-score?date=2026-07-30')
        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['reportingDate'], '2026-07-30')
        self.assertTrue(all(metric['score'] >= 0 for metric in data['metrics']))

    def test_trust_scores_explain_metric_confidence(self):
        response = self.client.get('/api/trust-score')
        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(data['metrics']), 4)
        self.assertTrue(all(0 <= metric['score'] <= 100 for metric in data['metrics']))
        self.assertTrue(all(len(metric['components']) == 4 and metric['explanation'] for metric in data['metrics']))

    def test_conflicts_are_enumerated_and_review_persists(self):
        response = self.client.get('/api/conflicts')
        data = response.get_json()
        self.assertGreater(len(data['items']), 3)
        self.assertTrue(any(item['type'] == 'OCCUPANCY' for item in data['items']))
        self.assertFalse(any(item['id'].startswith('ward-') for item in data['items']))
        self.assertIn('engine', data)
        self.assertTrue(all(item['ruleId'] and item['ruleName'] for item in data['items']))
        self.assertTrue(all('occurrences' in item and 'reviewed' in item for item in data['items']))
        conflict_id = data['items'][0]['id']
        self.assertEqual(self.client.post(f'/api/conflicts/{conflict_id}/review', json={'reviewed': True}).status_code, 200)
        self.assertTrue(next(item for item in self.client.get('/api/conflicts').get_json()['items'] if item['id'] == conflict_id)['reviewed'])
        self.client.post(f'/api/conflicts/{conflict_id}/review', json={'reviewed': False})


if __name__ == '__main__':
    unittest.main()
