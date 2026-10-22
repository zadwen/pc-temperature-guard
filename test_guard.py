import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import guard

class AlertTests(unittest.TestCase):
    def setUp(self):self.settings={**guard.DEFAULTS};self.alerts=guard.Alerts({})
    def sensor(self,temp=45,load=5):return guard.Sensor('cpu','CPU','cpu',temp,80,95,load)
    def test_brief_spike_does_not_alert(self):
        self.assertEqual(self.alerts.evaluate([self.sensor(85)],self.settings,100),[])
        self.assertEqual(self.alerts.evaluate([self.sensor(45)],self.settings,120),[])
    def test_sustained_heat_alerts(self):
        self.alerts.evaluate([self.sensor(85)],self.settings,100)
        self.assertTrue(any(e['key']=='cpu:heat' for e in self.alerts.evaluate([self.sensor(85)],self.settings,160)))
    def test_critical_is_immediate(self):self.assertTrue(self.alerts.evaluate([self.sensor(96)],self.settings,100)[0]['critical'])
    def test_high_load_does_not_suggest_dust(self):
        self.alerts.state['baselines']={'cpu':[40]*30}
        self.alerts.evaluate([self.sensor(65,90)],self.settings,100)
        self.assertEqual(self.alerts.evaluate([self.sensor(65,90)],self.settings,900),[])
    def test_sustained_low_load_baseline_rise(self):
        self.alerts.state['baselines']={'cpu':[40]*30}
        self.alerts.evaluate([self.sensor(65)],self.settings,100)
        events=self.alerts.evaluate([self.sensor(65)],self.settings,700)
        self.assertTrue(any(e['key']=='cpu:cooling' for e in events))
        self.assertIn('cannot confirm dust',events[0]['body'])
    def test_fan_zero_is_normal(self):
        sensor=guard.Sensor('gpu','GPU','gpu',45,80,90,2,0)
        self.assertEqual(self.alerts.evaluate([sensor],self.settings,100),[])
    def test_notification_rate_limit(self):
        event={'key':'hot','critical':False};calls=[]
        self.alerts.notify([event],100,lambda e:calls.append(e) or True)
        self.alerts.notify([event],120,lambda e:calls.append(e) or True)
        self.alerts.notify([event],1900,lambda e:calls.append(e) or True)
        self.assertEqual(len(calls),2)
    def test_failed_notification_retries(self):
        event={'key':'hot','critical':False};self.alerts.notify([event],100,lambda e:False)
        self.assertNotIn('hot',self.alerts.notified)
    def test_sensor_loss_needs_three_samples(self):
        self.alerts.evaluate([self.sensor()],self.settings,100)
        self.assertEqual(self.alerts.evaluate([],self.settings,120),[])
        self.assertEqual(self.alerts.evaluate([],self.settings,140),[])
        self.assertTrue(self.alerts.evaluate([],self.settings,160))
    def test_scheduled_cleaning(self):
        self.alerts.state['last_cleaned']=100
        self.assertTrue(any(e['key']=='cleaning' for e in self.alerts.evaluate([],self.settings,100+91*86400)))
    def test_invalid_hardware_limits_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            chip=Path(tmp)/'hwmon0';chip.mkdir()
            for name,value in {'name':'jc42','temp1_input':'40000','temp1_max':'0','temp1_crit':'0'}.items():(chip/name).write_text(value)
            sensors,_=guard.collect(self.settings,root=Path(tmp),gpu=False)
            self.assertEqual((sensors[0].warning,sensors[0].critical),(75,90))
    def test_cpu_load(self):self.assertEqual(guard.load_between((100,50),(200,125)),25)

if __name__=='__main__':unittest.main()
