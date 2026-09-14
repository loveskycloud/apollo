"""Mouse-only navigation for the docked Simulation Config / Tasks panel."""


async def sim_state(page):
    return await page.evaluate('()=>window._handle.get_simulation_state()')


async def sim_click(page, key):
    await page.wait_for_function('k=>window._handle.get_simulation_state()?.[k]', arg=key)
    await page.mouse.click(*(await sim_state(page))[key])
    await page.wait_for_timeout(450)


async def open_sim_tasks(page, task_id=''):
    state = await sim_state(page)
    if not state or not state.get('open'):
        await page.mouse.click(35, 340)
        await page.wait_for_function('()=>window._handle.get_simulation_state()?.open')
    if (await sim_state(page)).get('page') != 'tasks':
        await sim_click(page, 'tasks_tab')
    if (await sim_state(page))['filter_text'] != task_id:
        await sim_click(page, 'filter')
        await page.keyboard.press('Control+a')
        await page.keyboard.press('Backspace')
        if task_id:
            await page.keyboard.insert_text(task_id)
        await page.keyboard.press('Tab')
        await page.wait_for_timeout(450)
    rect = (await sim_state(page))['panel_rect']
    await page.mouse.move((rect[0]+rect[2])/2, rect[1]+250)
    await page.mouse.wheel(0, -10000)
    await page.wait_for_timeout(450)
